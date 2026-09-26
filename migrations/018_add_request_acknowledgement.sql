ALTER TABLE equipment
    ADD COLUMN review_acknowledged_at DATETIME NULL;

ALTER TABLE equipment_action_requests
    ADD COLUMN review_acknowledged_at DATETIME NULL;

ALTER TABLE organization_requests
    ADD COLUMN review_acknowledged_at DATETIME NULL;
