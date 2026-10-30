
USE fairshare;

INSERT INTO expense_categories (category_name, description) VALUES
    ('Food',           'Meals, snacks and drinks'),
    ('Transportation', 'Fare, fuel, tolls and parking'),
    ('Accommodation',  'Hotels, dorms and rentals'),
    ('Groceries',      'Supplies bought for shared use'),
    ('Utilities',      'Electricity, water, internet and load'),
    ('Other',          'Anything that does not fit elsewhere')
ON DUPLICATE KEY UPDATE category_name = category_name;
