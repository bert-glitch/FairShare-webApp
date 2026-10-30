
USE fairshare;

DROP EVENT IF EXISTS ev_mark_overdue_settlements;

CREATE EVENT ev_mark_overdue_settlements
    ON SCHEDULE EVERY 1 DAY
    STARTS (CURRENT_DATE + INTERVAL 1 DAY + INTERVAL 5 MINUTE)
    ON COMPLETION PRESERVE
    ENABLE
    COMMENT 'Marks pending settlements past their due date as overdue'
DO
    CALL sp_mark_overdue_settlements();
