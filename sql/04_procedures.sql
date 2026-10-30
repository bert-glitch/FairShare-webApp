
USE fairshare;
DELIMITER $$


DROP PROCEDURE IF EXISTS sp_lock_group$$
CREATE PROCEDURE sp_lock_group(IN p_group_id INT, IN p_require_active TINYINT)
BEGIN
    DECLARE v_id     INT         DEFAULT NULL;
    DECLARE v_status VARCHAR(10) DEFAULT NULL;

    SELECT group_id, status INTO v_id, v_status
    FROM expense_groups
    WHERE group_id = p_group_id
    FOR UPDATE;

    IF v_id IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The group does not exist.';
    END IF;
    IF p_require_active = 1 AND v_status <> 'active' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The group is archived, so no new activity can be recorded.';
    END IF;
END$$


DROP PROCEDURE IF EXISTS sp_create_group$$
CREATE PROCEDURE sp_create_group(
    IN  p_name        VARCHAR(100),
    IN  p_description VARCHAR(255),
    IN  p_created_by  INT,
    OUT p_group_id    INT)
BEGIN
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;
    INSERT INTO expense_groups (group_name, description, created_by)
    VALUES (p_name, p_description, p_created_by);
    SET p_group_id = LAST_INSERT_ID();

    INSERT INTO group_members (group_id, user_id) VALUES (p_group_id, p_created_by);
    COMMIT;
END$$


DROP PROCEDURE IF EXISTS sp_add_expense$$
CREATE PROCEDURE sp_add_expense(
    IN  p_group_id     INT,
    IN  p_paid_by      INT,
    IN  p_category_id  INT,
    IN  p_title        VARCHAR(100),
    IN  p_description  VARCHAR(255),
    IN  p_amount       DECIMAL(10,2),
    IN  p_expense_date DATE,
    IN  p_split_mode   VARCHAR(10),
    IN  p_shares       JSON,
    IN  p_created_by   INT,
    OUT p_expense_id   INT)
BEGIN
    DECLARE v_rows     INT DEFAULT 0;
    DECLARE v_valid    INT DEFAULT 0;
    DECLARE v_distinct INT DEFAULT 0;
    DECLARE v_cents    BIGINT;
    DECLARE v_base     BIGINT;
    DECLARE v_extra    BIGINT;
    DECLARE v_id       INT;
    DECLARE v_cat      INT DEFAULT NULL;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;
    CALL sp_lock_group(p_group_id, 1);

    IF p_split_mode IS NULL OR p_split_mode NOT IN ('equal', 'custom') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The split mode must be equal or custom.';
    END IF;
    IF p_shares IS NULL OR JSON_TYPE(p_shares) <> 'ARRAY' OR JSON_LENGTH(p_shares) = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Choose at least one member to share this expense.';
    END IF;

    SELECT category_id INTO v_cat
    FROM expense_categories
    WHERE category_id = p_category_id AND status = 'active';
    IF v_cat IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Choose a valid, active category.';
    END IF;

    -- check the share list is well formed BEFORE writing anything
    IF p_split_mode = 'equal' THEN
        SELECT COUNT(*), COUNT(j.uid), COUNT(DISTINCT j.uid) INTO v_rows, v_valid, v_distinct
        FROM JSON_TABLE(p_shares, '$[*]' COLUMNS (uid INT PATH '$')) j;
    ELSE
        SELECT COUNT(*),
               COUNT(CASE WHEN j.uid IS NOT NULL AND j.amt IS NOT NULL THEN 1 END),
               COUNT(DISTINCT j.uid)
        INTO v_rows, v_valid, v_distinct
        FROM JSON_TABLE(p_shares, '$[*]' COLUMNS (uid INT PATH '$.user_id',
                                                  amt DECIMAL(10,2) PATH '$.amount')) j;
    END IF;
    IF v_rows <> v_valid OR v_valid <> v_distinct THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Each selected member must appear once with a valid share.';
    END IF;

    -- 1) the expense starts as a DRAFT (it counts for nothing yet)
    INSERT INTO expenses (group_id, paid_by, category_id, created_by, expense_title,
                          description, amount, expense_date)
    VALUES (p_group_id, p_paid_by, p_category_id, p_created_by, p_title,
            p_description, p_amount, COALESCE(p_expense_date, CURDATE()));
    SET v_id = LAST_INSERT_ID();

    -- 2) the shares
    IF p_split_mode = 'equal' THEN
        -- exact integer arithmetic in centavos: base share for all, +1 centavo for the first r people
        SET v_cents = CAST(ROUND(p_amount * 100) AS SIGNED);
        SET v_base  = v_cents DIV v_distinct;
        SET v_extra = v_cents - v_base * v_distinct;

        INSERT INTO expense_splits (expense_id, group_id, user_id, share_amount)
        SELECT v_id, p_group_id, t.uid, (v_base + IF(t.rn <= v_extra, 1, 0)) / 100
        FROM (SELECT d.uid, ROW_NUMBER() OVER (ORDER BY d.uid) AS rn
              FROM (SELECT DISTINCT j.uid
                    FROM JSON_TABLE(p_shares, '$[*]' COLUMNS (uid INT PATH '$')) j) d) t;
    ELSE
        INSERT INTO expense_splits (expense_id, group_id, user_id, share_amount)
        SELECT v_id, p_group_id, j.uid, j.amt
        FROM JSON_TABLE(p_shares, '$[*]' COLUMNS (uid INT PATH '$.user_id',
                                                  amt DECIMAL(10,2) PATH '$.amount')) j;
    END IF;

    -- 3) activate: the trigger checks the shares add up exactly to the amount
    UPDATE expenses SET status = 'active' WHERE expense_id = v_id;

    COMMIT;
    SET p_expense_id = v_id;
END$$


DROP PROCEDURE IF EXISTS sp_archive_expense$$
CREATE PROCEDURE sp_archive_expense(IN p_expense_id INT)
BEGIN
    DECLARE v_group  INT         DEFAULT NULL;
    DECLARE v_status VARCHAR(10) DEFAULT NULL;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    SELECT group_id INTO v_group FROM expenses WHERE expense_id = p_expense_id;
    IF v_group IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The expense does not exist.';
    END IF;

    START TRANSACTION;
    CALL sp_lock_group(v_group, 1);

    SELECT status INTO v_status FROM expenses WHERE expense_id = p_expense_id FOR UPDATE;
    IF v_status = 'archived' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This expense is already archived.';
    END IF;

    UPDATE expenses SET status = 'archived' WHERE expense_id = p_expense_id;
    COMMIT;
END$$


DROP PROCEDURE IF EXISTS sp_record_settlement$$
CREATE PROCEDURE sp_record_settlement(
    IN  p_group_id        INT,
    IN  p_payer_id        INT,
    IN  p_receiver_id     INT,
    IN  p_amount          DECIMAL(10,2),
    IN  p_settlement_date DATE,
    IN  p_due_date        DATE,
    IN  p_mark_completed  TINYINT,
    IN  p_created_by      INT,
    OUT p_settlement_id   INT)
BEGIN
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;
    CALL sp_lock_group(p_group_id, 1);

    INSERT INTO settlements (group_id, payer_id, receiver_id, created_by, amount,
                             settlement_date, due_date, status)
    VALUES (p_group_id, p_payer_id, p_receiver_id, p_created_by, p_amount,
            COALESCE(p_settlement_date, CURDATE()), p_due_date,
            IF(p_mark_completed = 1, 'completed', 'pending'));
    SET p_settlement_id = LAST_INSERT_ID();

    COMMIT;
END$$


DROP PROCEDURE IF EXISTS sp_complete_settlement$$
CREATE PROCEDURE sp_complete_settlement(IN p_settlement_id INT)
BEGIN
    DECLARE v_group  INT         DEFAULT NULL;
    DECLARE v_status VARCHAR(10) DEFAULT NULL;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    SELECT group_id INTO v_group FROM settlements WHERE settlement_id = p_settlement_id;
    IF v_group IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The settlement does not exist.';
    END IF;

    START TRANSACTION;
    CALL sp_lock_group(v_group, 1);

    SELECT status INTO v_status FROM settlements WHERE settlement_id = p_settlement_id FOR UPDATE;
    IF v_status = 'completed' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This settlement is already completed.';
    END IF;
    IF v_status = 'cancelled' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A cancelled settlement cannot be completed.';
    END IF;

    UPDATE settlements SET status = 'completed' WHERE settlement_id = p_settlement_id;
    COMMIT;
END$$

-- ---------------------------------------------------------------------
-- sp_cancel_settlement(settlement_id)   pending/overdue -> cancelled (record is kept)
--   A COMPLETED settlement cannot be cancelled (there is no reversal process).
-- ---------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_cancel_settlement$$
CREATE PROCEDURE sp_cancel_settlement(IN p_settlement_id INT)
BEGIN
    DECLARE v_group  INT         DEFAULT NULL;
    DECLARE v_status VARCHAR(10) DEFAULT NULL;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    SELECT group_id INTO v_group FROM settlements WHERE settlement_id = p_settlement_id;
    IF v_group IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The settlement does not exist.';
    END IF;

    START TRANSACTION;
    CALL sp_lock_group(v_group, 1);

    SELECT status INTO v_status FROM settlements WHERE settlement_id = p_settlement_id FOR UPDATE;
    IF v_status = 'completed' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A completed settlement cannot be cancelled.';
    END IF;
    IF v_status = 'cancelled' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This settlement is already cancelled.';
    END IF;

    UPDATE settlements SET status = 'cancelled' WHERE settlement_id = p_settlement_id;
    COMMIT;
END$$


DROP PROCEDURE IF EXISTS sp_mark_overdue_settlements$$
CREATE PROCEDURE sp_mark_overdue_settlements()
BEGIN
    DECLARE v_done  INT DEFAULT 0;
    DECLARE v_gid   INT;
    DECLARE v_count INT DEFAULT 0;
    DECLARE cur CURSOR FOR
        SELECT DISTINCT group_id FROM settlements
        WHERE status = 'pending' AND due_date IS NOT NULL AND due_date < CURDATE()
        ORDER BY group_id;
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_done = 1;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    -- lock every affected group first, in group_id order (same "group first" order as everywhere else)
    OPEN cur;
    lock_loop: LOOP
        FETCH cur INTO v_gid;
        IF v_done = 1 THEN LEAVE lock_loop; END IF;
        CALL sp_lock_group(v_gid, 0);
    END LOOP;
    CLOSE cur;

    UPDATE settlements
    SET status = 'overdue'
    WHERE status = 'pending' AND due_date IS NOT NULL AND due_date < CURDATE();
    SET v_count = ROW_COUNT();

    COMMIT;
    SELECT v_count AS settlements_marked_overdue;
END$$

DELIMITER ;
