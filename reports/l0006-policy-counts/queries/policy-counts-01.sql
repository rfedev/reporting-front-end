-- Policy Counts Stage 1: Extract Active Policies
CREATE OR REPLACE TABLE `analytics_reporting.stage_active_policies` AS
SELECT
    p.policy_number,
    p.customer_id,
    p.effective_date,
    p.premium_amount,
    p.policy_status
FROM `raw_data.policies` AS p
WHERE p.effective_date >= '{repDate}'
  AND p.policy_status = '{policyStatus}';
