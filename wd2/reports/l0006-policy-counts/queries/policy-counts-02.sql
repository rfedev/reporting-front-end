-- Policy Counts Stage 2: Aggregate by Customer & Line of Business
CREATE OR REPLACE TABLE `analytics_reporting.stage_customer_aggregates` AS
SELECT
    ap.customer_id,
    c.customer_name,
    COUNT(ap.policy_number) AS total_policies,
    SUM(ap.premium_amount) AS total_premium
FROM `analytics_reporting.stage_active_policies` AS ap
JOIN `raw_data.customers` AS c ON ap.customer_id = c.id
WHERE ap.effective_date = '{repDate}'
GROUP BY ap.customer_id, c.customer_name;



-- Policy Counts Stage 2: Aggregate by Customer & Line of Business
CREATE OR REPLACE TABLE `analytics_reporting.stage_customer_aggregates2` AS
SELECT
    ap.customer_id,
    c.customer_name,
    COUNT(ap.policy_number) AS total_policies,
    SUM(ap.premium_amount) AS total_premium
FROM `analytics_reporting.stage_active_policies` AS ap
JOIN `raw_data.customers` AS c ON ap.customer_id = c.id
WHERE ap.effective_date = '{repDate}'
GROUP BY ap.customer_id, c.customer_name;
