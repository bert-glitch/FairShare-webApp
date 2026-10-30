
USE fairshare;
DELIMITER $$


DROP TRIGGER IF EXISTS trg_users_before_update$$
CREATE TRIGGER trg_users_before_update BEFORE UPDATE ON users FOR EACH ROW
BEGIN
    IF NEW.user_id <> OLD.user_id THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A user id cannot be changed.';
    END IF;
    IF NEW.role <> OLD.role THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The role of an account cannot be changed.';
    END IF;
    IF OLD.role = 'admin' AND NEW.is_active = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The administrator account cannot be deactivated.';
    END IF;
END$$

DROP TRIGGER IF EXISTS trg_users_before_delete$$
CREATE TRIGGER trg_users_before_delete BEFORE DELETE ON users FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Accounts cannot be deleted. Deactivate the account instead.';
END$$


DROP TRIGGER IF EXISTS trg_groups_before_update$$
CREATE TRIGGER trg_groups_before_update BEFORE UPDATE ON expense_groups FOR EACH ROW
BEGIN
    DECLARE v_open      INT DEFAULT 0;
    DECLARE v_unsettled INT DEFAULT 0;

    

    IF NEW.created_by <> OLD.created_by THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The creator of a group cannot be changed.';
    END IF;

    IF OLD.status = 'active' AND NEW.status = 'archived' THEN
        SELECT COUNT(*) INTO v_open
        FROM settlements
        WHERE group_id = OLD.group_id AND status IN ('pending', 'overdue')
        FOR SHARE;
        IF v_open > 0 THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Complete or cancel all open payments before archiving this group.';
        END IF;

        SELECT COUNT(*) INTO v_unsettled
        FROM group_members gm
        WHERE gm.group_id = OLD.group_id
          AND fn_member_net_balance(gm.group_id, gm.user_id) <> 0
        FOR SHARE;
        IF v_unsettled > 0 THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Everyone must be settled up (balance 0.00) before archiving this group.';
        END IF;
    END IF;
END$$


DROP TRIGGER IF EXISTS trg_group_members_before_insert$$
CREATE TRIGGER trg_group_members_before_insert BEFORE INSERT ON group_members FOR EACH ROW
BEGIN
    DECLARE v_active TINYINT DEFAULT NULL;

    CALL sp_lock_group(NEW.group_id, 1);

    IF NEW.status <> 'active' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A new membership must start as active.';
    END IF;

    SELECT is_active INTO v_active FROM users WHERE user_id = NEW.user_id FOR SHARE;
    IF v_active IS NULL OR v_active = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'That user account does not exist or is not active.';
    END IF;
END$$

DROP TRIGGER IF EXISTS trg_group_members_before_update$$
CREATE TRIGGER trg_group_members_before_update BEFORE UPDATE ON group_members FOR EACH ROW
BEGIN
    DECLARE v_active TINYINT DEFAULT NULL;
    DECLARE v_open   INT DEFAULT 0;

    CALL sp_lock_group(OLD.group_id, 1);

    IF NEW.group_id <> OLD.group_id OR NEW.user_id <> OLD.user_id OR NEW.joined_at <> OLD.joined_at THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only the status of a membership can be changed.';
    END IF;

    IF OLD.status = 'active' AND NEW.status = 'left' THEN
        -- leaving needs a zero balance and no open payments (otherwise the debt would be stranded)
        IF fn_member_net_balance(OLD.group_id, OLD.user_id) <> 0 THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This member still has a balance, so they cannot leave yet.';
        END IF;
        SELECT COUNT(*) INTO v_open
        FROM settlements
        WHERE group_id = OLD.group_id AND status IN ('pending', 'overdue')
          AND (payer_id = OLD.user_id OR receiver_id = OLD.user_id)
        FOR SHARE;
        IF v_open > 0 THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This member has open payments, so they cannot leave yet.';
        END IF;
    ELSEIF OLD.status = 'left' AND NEW.status = 'active' THEN
        SELECT is_active INTO v_active FROM users WHERE user_id = OLD.user_id FOR SHARE;
        IF v_active IS NULL OR v_active = 0 THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'That user account is not active.';
        END IF;
    END IF;
END$$


DROP TRIGGER IF EXISTS trg_expenses_before_insert$$
CREATE TRIGGER trg_expenses_before_insert BEFORE INSERT ON expenses FOR EACH ROW
BEGIN
    DECLARE v_cnt INT DEFAULT 0;

    CALL sp_lock_group(NEW.group_id, 1);

    IF NEW.status <> 'draft' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A new expense must start as a draft.';
    END IF;
    IF NEW.expense_date > CURDATE() THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The expense date cannot be in the future.';
    END IF;

    SELECT COUNT(*) INTO v_cnt
    FROM group_members
    WHERE group_id = NEW.group_id AND user_id = NEW.paid_by AND status = 'active'
    FOR SHARE;
    IF v_cnt = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The payer is not an active member of this group.';
    END IF;
END$$

DROP TRIGGER IF EXISTS trg_expenses_before_update$$
CREATE TRIGGER trg_expenses_before_update BEFORE UPDATE ON expenses FOR EACH ROW
BEGIN
    DECLARE v_msg VARCHAR(128) DEFAULT '';
    DECLARE v_count INT DEFAULT 0;
    DECLARE v_sum   DECIMAL(12,2) DEFAULT 0.00;

    CALL sp_lock_group(OLD.group_id, 1);

    IF NEW.group_id <> OLD.group_id OR NEW.paid_by <> OLD.paid_by OR NEW.category_id <> OLD.category_id
       OR NEW.created_by <> OLD.created_by OR NEW.amount <> OLD.amount
       OR NEW.expense_date <> OLD.expense_date OR NEW.expense_title <> OLD.expense_title
       OR NOT (NEW.description <=> OLD.description) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only the status of an expense can be changed.';
    END IF;

    IF OLD.status = 'archived' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'An archived expense cannot be changed.';
    END IF;

    IF NEW.status = OLD.status THEN
        SET NEW.status = OLD.status;
    ELSEIF OLD.status = 'draft' AND NEW.status = 'active' THEN
        SELECT COUNT(*) INTO v_count FROM expense_splits WHERE expense_id = OLD.expense_id FOR SHARE;
        IF v_count = 0 THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'An expense needs at least one share.';
        END IF;
        SET v_sum = fn_expense_split_total(OLD.expense_id);
        IF v_sum <> OLD.amount THEN
            SET v_msg = CONCAT('The shares add up to ', v_sum, ' but the expense amount is ', OLD.amount, '.');
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_msg;
        END IF;
    ELSEIF NEW.status = 'archived' THEN
        SET NEW.status = 'archived';
    ELSE
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'That status change is not allowed.';
    END IF;
END$$

DROP TRIGGER IF EXISTS trg_expenses_before_delete$$
CREATE TRIGGER trg_expenses_before_delete BEFORE DELETE ON expenses FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Expenses cannot be deleted. Archive the expense instead.';
END$$

-- =====================================================================
-- expense_splits: shares can only be ADDED while their expense is a draft, never changed
-- =====================================================================
DROP TRIGGER IF EXISTS trg_splits_before_insert$$
CREATE TRIGGER trg_splits_before_insert BEFORE INSERT ON expense_splits FOR EACH ROW
BEGIN
    DECLARE v_status VARCHAR(10) DEFAULT NULL;
    DECLARE v_cnt    INT DEFAULT 0;

    CALL sp_lock_group(NEW.group_id, 1);

    SELECT status INTO v_status FROM expenses WHERE expense_id = NEW.expense_id FOR SHARE;
    IF v_status IS NULL OR v_status <> 'draft' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Shares can only be added while the expense is a draft.';
    END IF;

    SELECT COUNT(*) INTO v_cnt
    FROM group_members
    WHERE group_id = NEW.group_id AND user_id = NEW.user_id AND status = 'active'
    FOR SHARE;
    IF v_cnt = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Every share must go to an active member of this group.';
    END IF;
END$$

DROP TRIGGER IF EXISTS trg_splits_before_update$$
CREATE TRIGGER trg_splits_before_update BEFORE UPDATE ON expense_splits FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Expense shares cannot be changed.';
END$$

DROP TRIGGER IF EXISTS trg_splits_before_delete$$
CREATE TRIGGER trg_splits_before_delete BEFORE DELETE ON expense_splits FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Expense shares cannot be deleted.';
END$$


DROP TRIGGER IF EXISTS trg_settlements_before_insert$$
CREATE TRIGGER trg_settlements_before_insert BEFORE INSERT ON settlements FOR EACH ROW
BEGIN
    DECLARE v_msg VARCHAR(128) DEFAULT '';
    DECLARE v_cnt            INT DEFAULT 0;
    DECLARE v_payer_avail    DECIMAL(12,2);
    DECLARE v_receiver_avail DECIMAL(12,2);

    CALL sp_lock_group(NEW.group_id, 1);

    IF NEW.amount IS NULL OR NEW.amount <= 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The settlement amount must be greater than zero.';
    END IF;
    IF NEW.payer_id = NEW.receiver_id THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A member cannot pay themselves.';
    END IF;

    SELECT COUNT(*) INTO v_cnt FROM group_members
    WHERE group_id = NEW.group_id AND user_id = NEW.payer_id AND status = 'active' FOR SHARE;
    IF v_cnt = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The payer is not an active member of this group.';
    END IF;
    SELECT COUNT(*) INTO v_cnt FROM group_members
    WHERE group_id = NEW.group_id AND user_id = NEW.receiver_id AND status = 'active' FOR SHARE;
    IF v_cnt = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The receiver is not an active member of this group.';
    END IF;

    IF NEW.status NOT IN ('pending', 'completed') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A new settlement must start as pending or completed.';
    END IF;
    IF NEW.status = 'pending' AND NEW.due_date IS NOT NULL AND NEW.due_date < CURDATE() THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The due date cannot be in the past.';
    END IF;
    IF NEW.status = 'completed' AND NEW.settlement_date > CURDATE() THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A completed payment cannot be dated in the future.';
    END IF;

    SET v_payer_avail = GREATEST(-fn_member_net_balance(NEW.group_id, NEW.payer_id), 0)
                        - fn_open_paying_commitments(NEW.group_id, NEW.payer_id, 0);
    SET v_receiver_avail = GREATEST(fn_member_net_balance(NEW.group_id, NEW.receiver_id), 0)
                        - fn_open_receiving_commitments(NEW.group_id, NEW.receiver_id, 0);

    IF NEW.amount > v_payer_avail THEN
        SET v_msg = CONCAT('That is more than the payer still owes (available: ', GREATEST(v_payer_avail, 0), ').');
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_msg;
    END IF;
    IF NEW.amount > v_receiver_avail THEN
        SET v_msg = CONCAT('That is more than the receiver is still owed (available: ', GREATEST(v_receiver_avail, 0), ').');
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_msg;
    END IF;
END$$

DROP TRIGGER IF EXISTS trg_settlements_before_update$$
CREATE TRIGGER trg_settlements_before_update BEFORE UPDATE ON settlements FOR EACH ROW
BEGIN
    DECLARE v_msg VARCHAR(128) DEFAULT '';
    DECLARE v_cnt            INT DEFAULT 0;
    DECLARE v_payer_avail    DECIMAL(12,2);
    DECLARE v_receiver_avail DECIMAL(12,2);

    CALL sp_lock_group(OLD.group_id, 1);

    IF NEW.group_id <> OLD.group_id OR NEW.payer_id <> OLD.payer_id OR NEW.receiver_id <> OLD.receiver_id
       OR NEW.created_by <> OLD.created_by OR NEW.amount <> OLD.amount
       OR NEW.settlement_date <> OLD.settlement_date OR NOT (NEW.due_date <=> OLD.due_date)
       OR NEW.created_at <> OLD.created_at THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Only the status of a settlement can be changed.';
    END IF;

    IF OLD.status IN ('completed', 'cancelled') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A completed or cancelled settlement is final.';
    END IF;

    IF NEW.status = OLD.status THEN
        SET NEW.status = OLD.status;
    ELSEIF NEW.status = 'overdue' THEN
        IF OLD.status <> 'pending' OR OLD.due_date IS NULL OR OLD.due_date >= CURDATE() THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A settlement is overdue only when it is pending and past its due date.';
        END IF;
    ELSEIF NEW.status = 'cancelled' THEN
        SET NEW.status = 'cancelled';
    ELSEIF NEW.status = 'completed' THEN
        SELECT COUNT(*) INTO v_cnt FROM group_members
        WHERE group_id = OLD.group_id AND user_id = OLD.payer_id AND status = 'active' FOR SHARE;
        IF v_cnt = 0 THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The payer is not an active member of this group.';
        END IF;
        SELECT COUNT(*) INTO v_cnt FROM group_members
        WHERE group_id = OLD.group_id AND user_id = OLD.receiver_id AND status = 'active' FOR SHARE;
        IF v_cnt = 0 THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The receiver is not an active member of this group.';
        END IF;

        SET v_payer_avail = GREATEST(-fn_member_net_balance(OLD.group_id, OLD.payer_id), 0)
                            - fn_open_paying_commitments(OLD.group_id, OLD.payer_id, OLD.settlement_id);
        SET v_receiver_avail = GREATEST(fn_member_net_balance(OLD.group_id, OLD.receiver_id), 0)
                            - fn_open_receiving_commitments(OLD.group_id, OLD.receiver_id, OLD.settlement_id);

        IF OLD.amount > v_payer_avail THEN
            SET v_msg = CONCAT('That is more than the payer still owes (available: ', GREATEST(v_payer_avail, 0), ').');
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_msg;
        END IF;
        IF OLD.amount > v_receiver_avail THEN
            SET v_msg = CONCAT('That is more than the receiver is still owed (available: ', GREATEST(v_receiver_avail, 0), ').');
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_msg;
        END IF;
    ELSE
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'That status change is not allowed.';
    END IF;
END$$

DROP TRIGGER IF EXISTS trg_settlements_before_delete$$
CREATE TRIGGER trg_settlements_before_delete BEFORE DELETE ON settlements FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Settlements cannot be deleted. Cancel the settlement instead.';
END$$

DELIMITER ;
