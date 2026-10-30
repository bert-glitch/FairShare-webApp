
USE fairshare;


CREATE OR REPLACE VIEW vw_member_balances AS
SELECT
    b.*,
    GREATEST(-b.net_balance, 0) AS amount_owed_by_member,
    GREATEST( b.net_balance, 0) AS amount_owed_to_member
FROM (
    SELECT
        gm.group_id,
        eg.group_name,
        gm.user_id,
        u.full_name,
        u.username,
        gm.status AS membership_status,
        COALESCE(p.total_paid, 0)        AS total_paid,
        COALESCE(s.total_share, 0)       AS total_share,
        COALESCE(sp.settled_paid, 0)     AS settlements_paid,
        COALESCE(sr.settled_received, 0) AS settlements_received,
        COALESCE(p.total_paid, 0) - COALESCE(s.total_share, 0)
          + COALESCE(sp.settled_paid, 0) - COALESCE(sr.settled_received, 0) AS net_balance
    FROM group_members gm
    JOIN expense_groups eg ON eg.group_id = gm.group_id
    JOIN users u           ON u.user_id   = gm.user_id
    LEFT JOIN (
        SELECT group_id, paid_by AS user_id, SUM(amount) AS total_paid
        FROM expenses WHERE status = 'active'
        GROUP BY group_id, paid_by
    ) p  ON p.group_id = gm.group_id AND p.user_id = gm.user_id
    LEFT JOIN (
        SELECT e.group_id, es.user_id, SUM(es.share_amount) AS total_share
        FROM expense_splits es
        JOIN expenses e ON e.expense_id = es.expense_id AND e.group_id = es.group_id
        WHERE e.status = 'active'
        GROUP BY e.group_id, es.user_id
    ) s  ON s.group_id = gm.group_id AND s.user_id = gm.user_id
    LEFT JOIN (
        SELECT group_id, payer_id AS user_id, SUM(amount) AS settled_paid
        FROM settlements WHERE status = 'completed'
        GROUP BY group_id, payer_id
    ) sp ON sp.group_id = gm.group_id AND sp.user_id = gm.user_id
    LEFT JOIN (
        SELECT group_id, receiver_id AS user_id, SUM(amount) AS settled_received
        FROM settlements WHERE status = 'completed'
        GROUP BY group_id, receiver_id
    ) sr ON sr.group_id = gm.group_id AND sr.user_id = gm.user_id
) b;

-- ---------------------------------------------------------------------
-- vw_group_expense_summary: one row per group
--   Example: SELECT * FROM vw_group_expense_summary ORDER BY group_name;
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_group_expense_summary AS
SELECT
    eg.group_id,
    eg.group_name,
    eg.description,
    eg.status                AS group_status,
    eg.created_by,
    cu.full_name             AS created_by_name,
    eg.created_at,
    COALESCE(m.active_members, 0)          AS active_member_count,
    COALESCE(ex.active_count, 0)           AS active_expense_count,
    COALESCE(ex.active_total, 0.00)        AS active_expense_total,
    COALESCE(ex.archived_count, 0)         AS archived_expense_count,
    COALESCE(st.completed_total, 0.00)     AS completed_settlement_total,
    COALESCE(st.open_count, 0)             AS open_settlement_count,
    COALESCE(st.open_total, 0.00)          AS open_settlement_total,
    COALESCE(st.overdue_count, 0)          AS overdue_settlement_count
FROM expense_groups eg
JOIN users cu ON cu.user_id = eg.created_by
LEFT JOIN (
    SELECT group_id, COUNT(*) AS active_members
    FROM group_members WHERE status = 'active' GROUP BY group_id
) m ON m.group_id = eg.group_id
LEFT JOIN (
    SELECT group_id,
           SUM(CASE WHEN status = 'active'   THEN 1      ELSE 0 END) AS active_count,
           SUM(CASE WHEN status = 'active'   THEN amount ELSE 0 END) AS active_total,
           SUM(CASE WHEN status = 'archived' THEN 1      ELSE 0 END) AS archived_count
    FROM expenses GROUP BY group_id
) ex ON ex.group_id = eg.group_id
LEFT JOIN (
    SELECT group_id,
           SUM(CASE WHEN status = 'completed'             THEN amount ELSE 0 END) AS completed_total,
           SUM(CASE WHEN status IN ('pending', 'overdue') THEN 1      ELSE 0 END) AS open_count,
           SUM(CASE WHEN status IN ('pending', 'overdue') THEN amount ELSE 0 END) AS open_total,
           SUM(CASE WHEN status = 'overdue'               THEN 1      ELSE 0 END) AS overdue_count
    FROM settlements GROUP BY group_id
) st ON st.group_id = eg.group_id;


CREATE OR REPLACE VIEW vw_expense_split_check AS
SELECT
    e.expense_id,
    e.group_id,
    e.expense_title,
    e.status                          AS expense_status,
    e.amount                          AS expense_amount,
    COALESCE(s.allocated_total, 0.00) AS allocated_total,
    e.amount - COALESCE(s.allocated_total, 0.00) AS difference,
    COALESCE(s.share_count, 0)        AS share_count,
    CASE WHEN COALESCE(s.share_count, 0) > 0
          AND e.amount = COALESCE(s.allocated_total, 0.00) THEN 1 ELSE 0 END AS is_balanced
FROM expenses e
LEFT JOIN (
    SELECT expense_id, SUM(share_amount) AS allocated_total, COUNT(*) AS share_count
    FROM expense_splits GROUP BY expense_id
) s ON s.expense_id = e.expense_id;
