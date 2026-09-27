UPDATE equipment e
JOIN accountability a
    ON a.equipment_id = e.id
   AND a.is_current = 1
SET e.status = 'Assigned'
WHERE e.status = 'Available';
