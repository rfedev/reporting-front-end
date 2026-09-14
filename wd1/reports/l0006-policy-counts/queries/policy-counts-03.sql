-- Policy Counts Stage 3: Final Report Output
CREATE OR REPLACE TABLE `analytics_reporting.final_policy_counts_report` AS
SELECT
    ca.customer_name,
    ca.total_policies,
    ca.total_premium,
    CURRENT_TIMESTAMP() AS generated_at
FROM `analytics_reporting.stage_customer_aggregates` AS ca
WHERE ca.total_policies > 0;
