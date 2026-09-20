UPDATE transactions
SET action = CASE
    WHEN action = 'Requested' AND details LIKE '%equipment submitted%' THEN 'Requested/Create'
    WHEN action = 'Requested' AND details LIKE '%requested edit%' THEN 'Requested/Edit'
    WHEN action = 'Requested' AND details LIKE '%requested assign%' THEN 'Requested/Assign'
    WHEN action = 'Requested' AND details LIKE '%requested maintenance start%' THEN 'Requested/Maintenance Start'
    WHEN action = 'Requested' AND details LIKE '%requested maintenance complete%' THEN 'Requested/Maintenance Complete'
    WHEN action = 'Requested' AND details LIKE '%requested archive%' THEN 'Requested/Archive'
    WHEN action = 'Requested' AND details LIKE '%requested dispose%' THEN 'Requested/Dispose'
    WHEN action = 'Approved' AND details LIKE '%equipment request approved%' THEN 'Approved/Create'
    WHEN action = 'Approved' AND details LIKE '%edit request approved%' THEN 'Approved/Edit'
    WHEN action = 'Approved' AND details LIKE '%assignment request approved%' THEN 'Approved/Assign'
    WHEN action = 'Approved' AND details LIKE '%maintenance start request approved%' THEN 'Approved/Maintenance Start'
    WHEN action = 'Approved' AND details LIKE '%maintenance complete request approved%' THEN 'Approved/Maintenance Complete'
    WHEN action = 'Approved' AND details LIKE '%archive request approved%' THEN 'Approved/Archive'
    WHEN action = 'Approved' AND details LIKE '%disposal request approved%' THEN 'Approved/Dispose'
    WHEN action = 'Rejected' THEN 'Rejected/Create'
    ELSE action
END
WHERE action IN ('Requested', 'Approved', 'Rejected');
