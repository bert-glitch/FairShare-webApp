
USE fairshare;

CREATE TABLE IF NOT EXISTS users (
    user_id        INT          NOT NULL AUTO_INCREMENT,
    full_name      VARCHAR(100) NOT NULL,
    username       VARCHAR(50)  NOT NULL,
    email          VARCHAR(100) NULL,
    password_hash  VARCHAR(255) NOT NULL,
    role           VARCHAR(10)  NOT NULL DEFAULT 'user',
    is_active      TINYINT(1)   NOT NULL DEFAULT 1,
    admin_slot     TINYINT GENERATED ALWAYS AS (IF(role = 'admin', 1, NULL)) STORED,
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id),
    UNIQUE KEY uq_users_username (username),
    UNIQUE KEY uq_users_email (email),
    UNIQUE KEY uq_users_single_admin (admin_slot),
    CONSTRAINT ck_users_role     CHECK (role IN ('admin', 'user')),
    CONSTRAINT ck_users_active   CHECK (is_active IN (0, 1)),
    CONSTRAINT ck_users_name     CHECK (CHAR_LENGTH(TRIM(full_name)) > 0),
    CONSTRAINT ck_users_username CHECK (username REGEXP '^[A-Za-z0-9_.]{3,50}$'),
    CONSTRAINT ck_users_email    CHECK (email IS NULL OR email LIKE '%_@_%._%')
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS expense_groups (
    group_id     INT          NOT NULL AUTO_INCREMENT,
    group_name   VARCHAR(100) NOT NULL,
    description  VARCHAR(255) NULL,
    created_by   INT          NOT NULL,
    status       VARCHAR(10)  NOT NULL DEFAULT 'active',
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (group_id),
    CONSTRAINT fk_groups_creator FOREIGN KEY (created_by) REFERENCES users (user_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_groups_name   CHECK (CHAR_LENGTH(TRIM(group_name)) > 0),
    CONSTRAINT ck_groups_status CHECK (status IN ('active', 'archived'))
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS group_members (
    group_member_id INT         NOT NULL AUTO_INCREMENT,
    group_id        INT         NOT NULL,
    user_id         INT         NOT NULL,
    status          VARCHAR(10) NOT NULL DEFAULT 'active',
    joined_at       DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (group_member_id),
    UNIQUE KEY uq_group_members_pair (group_id, user_id),
    CONSTRAINT fk_gm_group FOREIGN KEY (group_id) REFERENCES expense_groups (group_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_gm_user  FOREIGN KEY (user_id)  REFERENCES users (user_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_gm_status CHECK (status IN ('active', 'left'))
) ENGINE=InnoDB;


CREATE TABLE IF NOT EXISTS expense_categories (
    category_id    INT          NOT NULL AUTO_INCREMENT,
    category_name  VARCHAR(50)  NOT NULL,
    description    VARCHAR(255) NULL,
    status         VARCHAR(10)  NOT NULL DEFAULT 'active',
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (category_id),
    UNIQUE KEY uq_category_name (category_name),
    CONSTRAINT ck_cat_name   CHECK (CHAR_LENGTH(TRIM(category_name)) > 0),
    CONSTRAINT ck_cat_status CHECK (status IN ('active', 'inactive'))
) ENGINE=InnoDB;


CREATE TABLE IF NOT EXISTS expenses (
    expense_id     INT           NOT NULL AUTO_INCREMENT,
    group_id       INT           NOT NULL,
    paid_by        INT           NOT NULL,
    category_id    INT           NOT NULL,
    created_by     INT           NOT NULL,
    expense_title  VARCHAR(100)  NOT NULL,
    description    VARCHAR(255)  NULL,
    amount         DECIMAL(10,2) NOT NULL,
    expense_date   DATE          NOT NULL,
    status         VARCHAR(10)   NOT NULL DEFAULT 'draft',
    created_at     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (expense_id),
    UNIQUE KEY uq_expenses_id_group (expense_id, group_id),
    KEY idx_exp_group_status_date (group_id, status, expense_date),
    CONSTRAINT fk_exp_payer    FOREIGN KEY (group_id, paid_by) REFERENCES group_members (group_id, user_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_exp_category FOREIGN KEY (category_id) REFERENCES expense_categories (category_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_exp_creator  FOREIGN KEY (created_by)  REFERENCES users (user_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_exp_title  CHECK (CHAR_LENGTH(TRIM(expense_title)) > 0),
    CONSTRAINT ck_exp_amount CHECK (amount > 0),
    CONSTRAINT ck_exp_status CHECK (status IN ('draft', 'active', 'archived'))
) ENGINE=InnoDB;


CREATE TABLE IF NOT EXISTS expense_splits (
    expense_split_id INT           NOT NULL AUTO_INCREMENT,
    expense_id       INT           NOT NULL,
    group_id         INT           NOT NULL,
    user_id          INT           NOT NULL,
    share_amount     DECIMAL(10,2) NOT NULL,
    created_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (expense_split_id),
    UNIQUE KEY uq_split_expense_user (expense_id, user_id),
    CONSTRAINT fk_split_expense FOREIGN KEY (expense_id, group_id) REFERENCES expenses (expense_id, group_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_split_member  FOREIGN KEY (group_id, user_id) REFERENCES group_members (group_id, user_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_split_share CHECK (share_amount >= 0)
) ENGINE=InnoDB;


CREATE TABLE IF NOT EXISTS settlements (
    settlement_id    INT           NOT NULL AUTO_INCREMENT,
    group_id         INT           NOT NULL,
    payer_id         INT           NOT NULL,
    receiver_id      INT           NOT NULL,
    created_by       INT           NOT NULL,
    amount           DECIMAL(10,2) NOT NULL,
    settlement_date  DATE          NOT NULL,
    due_date         DATE          NULL,
    status           VARCHAR(10)   NOT NULL DEFAULT 'pending',
    created_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (settlement_id),
    KEY idx_stl_group_status (group_id, status),
    KEY idx_stl_status_due (status, due_date),
    CONSTRAINT fk_stl_payer    FOREIGN KEY (group_id, payer_id)    REFERENCES group_members (group_id, user_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_stl_receiver FOREIGN KEY (group_id, receiver_id) REFERENCES group_members (group_id, user_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_stl_creator  FOREIGN KEY (created_by) REFERENCES users (user_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_stl_status CHECK (status IN ('pending', 'completed', 'overdue', 'cancelled')),
    CONSTRAINT ck_stl_due    CHECK (due_date IS NULL OR due_date >= settlement_date)
) ENGINE=InnoDB;
