
USE fairshare;


-- A1. Seven core tables (plus the 3 views)           EXPECT: 7 BASE TABLE rows, 3 VIEW rows
SHOW FULL TABLES;

-- A2. All foreign keys; the COMPOSITE ones have two columns
--                                                    EXPECT: 11 constraints; fk_exp_payer, fk_split_expense,
--                                                            fk_split_member, fk_stl_payer, fk_stl_receiver have 2 columns
SELECT constraint_name, table_name, GROUP_CONCAT(column_name ORDER BY ordinal_position) AS columns_used,
       COUNT(*) AS column_count, MAX(referenced_table_name) AS references_table
FROM information_schema.key_column_usage
WHERE table_schema = DATABASE() AND referenced_table_name IS NOT NULL
GROUP BY constraint_name, table_name
ORDER BY column_count DESC, constraint_name;

-- A3. Unique keys and indexes                        EXPECT: uq_* keys and idx_* indexes listed
SELECT table_name, index_name, IF(non_unique = 0, 'UNIQUE', '') AS is_unique,
       GROUP_CONCAT(column_name ORDER BY seq_in_index) AS columns_indexed
FROM information_schema.statistics WHERE table_schema = DATABASE()
GROUP BY table_name, index_name, non_unique ORDER BY table_name, index_name;

-- ---------------------------------------------------------------------
-- B. ACCOUNTING
-- ---------------------------------------------------------------------
-- B1. Total shares equal the expense amount (active expenses)     EXPECT: no rows
SELECT * FROM vw_expense_split_check WHERE expense_status = 'active' AND is_balanced = 0;

-- B2. Group balances add up to zero                               EXPECT: no rows
SELECT group_id, SUM(net_balance) AS sum_of_balances
FROM vw_member_balances GROUP BY group_id HAVING SUM(net_balance) <> 0;

-- B3. Completed settlements affect balances exactly once.
--     Money paid out must equal money received out, per group.     EXPECT: no rows
SELECT group_id, SUM(settlements_paid) AS paid, SUM(settlements_received) AS received
FROM vw_member_balances GROUP BY group_id HAVING SUM(settlements_paid) <> SUM(settlements_received);

-- B4. The view equals an independent calculation that counts ONLY completed settlements
--     (so pending, overdue and cancelled ones are provably ignored).  EXPECT: no rows
SELECT v.group_id, v.user_id, v.net_balance AS view_value, x.expected
FROM vw_member_balances v
JOIN (
    SELECT gm.group_id, gm.user_id,
           COALESCE((SELECT SUM(e.amount) FROM expenses e WHERE e.group_id = gm.group_id AND e.paid_by = gm.user_id AND e.status = 'active'), 0)
         - COALESCE((SELECT SUM(s.share_amount) FROM expense_splits s JOIN expenses e ON e.expense_id = s.expense_id
                     WHERE s.group_id = gm.group_id AND s.user_id = gm.user_id AND e.status = 'active'), 0)
         + COALESCE((SELECT SUM(t.amount) FROM settlements t WHERE t.group_id = gm.group_id AND t.payer_id = gm.user_id AND t.status = 'completed'), 0)
         - COALESCE((SELECT SUM(t.amount) FROM settlements t WHERE t.group_id = gm.group_id AND t.receiver_id = gm.user_id AND t.status = 'completed'), 0) AS expected
    FROM group_members gm
) x ON x.group_id = v.group_id AND x.user_id = v.user_id
WHERE v.net_balance <> x.expected;

-- B5. Archived expenses are excluded from BOTH sides (paid and shares) consistently.
--     Total paid must equal total shares in every group.             EXPECT: no rows
SELECT group_id, SUM(total_paid) AS total_paid, SUM(total_share) AS total_share
FROM vw_member_balances GROUP BY group_id HAVING SUM(total_paid) <> SUM(total_share);

-- B6. The stored function agrees with the view for every member      EXPECT: no rows
SELECT v.group_id, v.user_id, v.total_share AS view_value, fn_member_total_share(v.group_id, v.user_id) AS function_value
FROM vw_member_balances v WHERE v.total_share <> fn_member_total_share(v.group_id, v.user_id);

-- B7. The write-time balance function agrees with the view           EXPECT: no rows
SELECT v.group_id, v.user_id, v.net_balance AS view_value, fn_member_net_balance(v.group_id, v.user_id) AS function_value
FROM vw_member_balances v WHERE v.net_balance <> fn_member_net_balance(v.group_id, v.user_id);

-- B8. Settlement statuses in use                                     EXPECT: only pending/completed/overdue/cancelled
SELECT status, COUNT(*) AS settlements, SUM(amount) AS total_amount FROM settlements GROUP BY status;

-- B9. No unfinished drafts left behind (every failed expense rolled back)   EXPECT: no rows
SELECT * FROM vw_expense_split_check WHERE expense_status = 'draft';

-- ---------------------------------------------------------------------
-- C. DATABASE OBJECTS
-- ---------------------------------------------------------------------
-- C1. Views                                          EXPECT: 3
SELECT table_name AS view_name FROM information_schema.views WHERE table_schema = DATABASE();
-- C2. Stored functions                               EXPECT: 5
SHOW FUNCTION STATUS WHERE Db = DATABASE();
-- C3. Stored procedures                              EXPECT: 8
SHOW PROCEDURE STATUS WHERE Db = DATABASE();
-- C4. Triggers                                       EXPECT: 14
SHOW TRIGGERS;
-- C5. The scheduled event                            EXPECT: ev_mark_overdue_settlements, ENABLED, every 1 DAY
SHOW EVENTS FROM fairshare;
SELECT event_name, status, interval_value, interval_field, starts, last_executed
FROM information_schema.events WHERE event_schema = DATABASE();
-- C6. Is the event scheduler on?                     EXPECT: ON   (if OFF run:  SET GLOBAL event_scheduler = ON;)
SHOW VARIABLES LIKE 'event_scheduler';

-- ---------------------------------------------------------------------
-- D. ADMINISTRATOR RULE
-- ---------------------------------------------------------------------
-- D1. At most one administrator (exactly one after setup_admin.py)   EXPECT: 1
SELECT COUNT(*) AS administrators FROM users WHERE role = 'admin';
-- D2. No password is stored in plain text                            EXPECT: no rows
--     (real hashes start with scrypt: or pbkdf2:; '!' marks a locked sample account)
SELECT user_id, username FROM users WHERE password_hash NOT LIKE 'scrypt:%' AND password_hash NOT LIKE 'pbkdf2:%' AND password_hash <> '!';
