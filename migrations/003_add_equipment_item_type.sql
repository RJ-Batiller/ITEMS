ALTER TABLE equipment
    ADD COLUMN item_type ENUM('Consumable','Non-Consumable') NOT NULL DEFAULT 'Non-Consumable'
    AFTER acquisition_date;
