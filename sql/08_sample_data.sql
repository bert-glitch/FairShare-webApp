
USE fairshare;

DROP PROCEDURE IF EXISTS tmp_load_sample_data;
DELIMITER $$
CREATE PROCEDURE tmp_load_sample_data()
BEGIN
    DECLARE v_ana INT; DECLARE v_ben INT; DECLARE v_carla INT; DECLARE v_dan INT;
    DECLARE v_g1 INT;  DECLARE v_g2 INT;  DECLARE v_id INT;
    DECLARE v_food INT; DECLARE v_util INT; DECLARE v_groc INT; DECLARE v_trans INT;
    DECLARE v_hotel INT; DECLARE v_other INT;

    IF EXISTS (SELECT 1 FROM users WHERE username = 'ana') THEN
        SELECT 'Sample data is already loaded. Nothing was changed.' AS message;
    ELSE
        INSERT INTO users (full_name, username, email, password_hash) VALUES
            ('Junbert Inagong',    'bert',   'bert@example.com',   '!'),
            ('Donnie Amacna',     'don',   'don@example.com',   '!'),
            ('Ledmi Aponda',     'led', 'led@example.com', '!'),
            ('Jessean Bacus',   'jess',   'jes@example.com',   '!');
        SELECT user_id INTO v_ana   FROM users WHERE username = 'bert';
        SELECT user_id INTO v_ben   FROM users WHERE username = 'don';
        SELECT user_id INTO v_carla FROM users WHERE username = 'led';
        SELECT user_id INTO v_dan   FROM users WHERE username = 'jess';

        SELECT category_id INTO v_food  FROM expense_categories WHERE category_name = 'Food';
        SELECT category_id INTO v_util  FROM expense_categories WHERE category_name = 'Utilities';
        SELECT category_id INTO v_groc  FROM expense_categories WHERE category_name = 'Groceries';
        SELECT category_id INTO v_trans FROM expense_categories WHERE category_name = 'Transportation';
        SELECT category_id INTO v_hotel FROM expense_categories WHERE category_name = 'Accommodation';
        SELECT category_id INTO v_other FROM expense_categories WHERE category_name = 'Other';

        -- Group 1: Capstone Project Team (bert, don, led, jess)
        CALL sp_create_group('Capstone Project Team', 'Shared costs for our capstone', v_ana, v_g1);
        INSERT INTO group_members (group_id, user_id) VALUES (v_g1, v_ben), (v_g1, v_carla), (v_g1, v_dan);

        CALL sp_add_expense(v_g1, v_bert, v_other, 'Printing of manuscript', 'Final hard copies',
             900.00, CURDATE() - INTERVAL 14 DAY, 'equal', JSON_ARRAY(v_ana, v_ben, v_carla), v_ana, v_id);
        CALL sp_add_expense(v_g1, v_don, v_util, 'Domain and hosting', 'One year',
             1000.00, CURDATE() - INTERVAL 12 DAY, 'equal', JSON_ARRAY(v_ana, v_ben, v_carla, v_dan), v_ben, v_id);
        CALL sp_add_expense(v_g1, v_led, v_food, 'Team lunch', 'Does not divide evenly: 33.34 / 33.33 / 33.33',
             100.00, CURDATE() - INTERVAL 9 DAY, 'equal', JSON_ARRAY(v_ana, v_ben, v_carla), v_carla, v_id);
        CALL sp_add_expense(v_g1, v_jess, v_groc, 'Prototype materials', 'Custom split by usage',
             650.50, CURDATE() - INTERVAL 6 DAY, 'custom',
             JSON_ARRAY(JSON_OBJECT('user_id', v_bert,   'amount', '200.00'),
                        JSON_OBJECT('user_id', v_don,   'amount', '150.50'),
                        JSON_OBJECT('user_id', v_led, 'amount', '200.00'),
                        JSON_OBJECT('user_id', v_jess,   'amount', '100.00')), v_jess, v_id);

        -- settlements in every status
        CALL sp_record_settlement(v_g1, v_bert, v_don, 200.00, CURDATE() - INTERVAL 3 DAY, NULL, 1, v_dan, v_id);   -- completed
        CALL sp_record_settlement(v_g1, v_bert, v_led, 100.00, CURDATE(), CURDATE() + INTERVAL 7 DAY, 0, v_carla, v_id); -- pending
        CALL sp_record_settlement(v_g1, v_bert, v_jess,  30.00, CURDATE(), CURDATE() + INTERVAL 5 DAY, 0, v_carla, v_id); -- will be cancelled
        CALL sp_cancel_settlement(v_id);                                                                              -- cancelled

        SET TIMESTAMP = UNIX_TIMESTAMP(NOW() - INTERVAL 10 DAY);       -- temporary clock, 10 days back
        CALL sp_record_settlement(v_g1, v_bert, v_jess, 50.00, CURDATE(), CURDATE() + INTERVAL 3 DAY, 0, v_carla, v_id);
        SET TIMESTAMP = DEFAULT;                                       -- clock restored
        CALL sp_mark_overdue_settlements();                            -- the overdue one becomes OVERDUE

        -- Group 2: Baguio Trip (bert, led, don)
        CALL sp_create_group('Baguio Trip', 'Barkada trip expenses', v_bert, v_g2);
        INSERT INTO group_members (group_id, user_id) VALUES (v_g2, v_led), (v_g2, v_don);
        CALL sp_add_expense(v_g2, v_bert, v_hotel, 'Hotel (2 nights)', NULL,
             4500.00, CURDATE() - INTERVAL 20 DAY, 'equal', JSON_ARRAY(v_led, v_bert, v_don), v_bert, v_id);
        CALL sp_add_expense(v_g2, v_led, v_trans, 'Gas and tolls', 'Another uneven split',
             1250.75, CURDATE() - INTERVAL 19 DAY, 'equal', JSON_ARRAY(v_ana, v_ben, v_carla), v_ana, v_id);

        SELECT 'Sample data loaded: 4 users, 2 groups, 6 expenses, 4 settlements.' AS message;
    END IF;
END$$
DELIMITER ;

CALL tmp_load_sample_data();
DROP PROCEDURE tmp_load_sample_data;
