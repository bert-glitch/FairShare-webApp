
USE fairshare;
DELIMITER $$


DROP FUNCTION IF EXISTS fn_member_total_share$$
CREATE FUNCTION fn_member_total_share(p_group_id INT, p_user_id INT)
RETURNS DECIMAL(12,2)
READS SQL DATA
BEGIN
    DECLARE v_total DECIMAL(12,2) DEFAULT 0.00;

    SELECT COALESCE(SUM(es.share_amount), 0.00) INTO v_total
    FROM expense_splits es
    JOIN expenses e ON e.expense_id = es.expense_id AND e.group_id = es.group_id
    WHERE es.group_id = p_group_id
      AND es.user_id  = p_user_id
      AND e.status    = 'active';

    RETURN COALESCE(v_total, 0.00);
END$$


DROP FUNCTION IF EXISTS fn_expense_split_total$$
CREATE FUNCTION fn_expense_split_total(p_expense_id INT)
RETURNS DECIMAL(12,2)
READS SQL DATA
BEGIN
    DECLARE v_total DECIMAL(12,2) DEFAULT 0.00;
    SELECT COALESCE(SUM(share_amount), 0.00) INTO v_total
    FROM expense_splits
    WHERE expense_id = p_expense_id;
    RETURN COALESCE(v_total, 0.00);
END$$


DROP FUNCTION IF EXISTS fn_member_net_balance$$
CREATE FUNCTION fn_member_net_balance(p_group_id INT, p_user_id INT)
RETURNS DECIMAL(12,2)
READS SQL DATA
BEGIN
    DECLARE v_paid  DECIMAL(12,2) DEFAULT 0.00;
    DECLARE v_share DECIMAL(12,2) DEFAULT 0.00;
    DECLARE v_sp    DECIMAL(12,2) DEFAULT 0.00;
    DECLARE v_sr    DECIMAL(12,2) DEFAULT 0.00;

    SELECT COALESCE(SUM(amount), 0.00) INTO v_paid
    FROM expenses
    WHERE group_id = p_group_id AND paid_by = p_user_id AND status = 'active'
    FOR SHARE;

    SELECT COALESCE(SUM(es.share_amount), 0.00) INTO v_share
    FROM expense_splits es
    JOIN expenses e ON e.expense_id = es.expense_id
    WHERE e.group_id = p_group_id AND es.user_id = p_user_id AND e.status = 'active'
    FOR SHARE;

    SELECT COALESCE(SUM(amount), 0.00) INTO v_sp
    FROM settlements
    WHERE group_id = p_group_id AND payer_id = p_user_id AND status = 'completed'
    FOR SHARE;

    SELECT COALESCE(SUM(amount), 0.00) INTO v_sr
    FROM settlements
    WHERE group_id = p_group_id AND receiver_id = p_user_id AND status = 'completed'
    FOR SHARE;

    RETURN v_paid - v_share + v_sp - v_sr;
END$$


DROP FUNCTION IF EXISTS fn_open_paying_commitments$$
CREATE FUNCTION fn_open_paying_commitments(p_group_id INT, p_user_id INT, p_exclude_id INT)
RETURNS DECIMAL(12,2)
READS SQL DATA
BEGIN
    DECLARE v_total DECIMAL(12,2) DEFAULT 0.00;
    SELECT COALESCE(SUM(amount), 0.00) INTO v_total
    FROM settlements
    WHERE group_id = p_group_id AND payer_id = p_user_id
      AND status IN ('pending', 'overdue')
      AND settlement_id <> p_exclude_id
    FOR SHARE;
    RETURN v_total;
END$$

-- Same idea for the member's open payments as RECEIVER.
DROP FUNCTION IF EXISTS fn_open_receiving_commitments$$
CREATE FUNCTION fn_open_receiving_commitments(p_group_id INT, p_user_id INT, p_exclude_id INT)
RETURNS DECIMAL(12,2)
READS SQL DATA
BEGIN
    DECLARE v_total DECIMAL(12,2) DEFAULT 0.00;
    SELECT COALESCE(SUM(amount), 0.00) INTO v_total
    FROM settlements
    WHERE group_id = p_group_id AND receiver_id = p_user_id
      AND status IN ('pending', 'overdue')
      AND settlement_id <> p_exclude_id
    FOR SHARE;
    RETURN v_total;
END$$

DELIMITER ;
