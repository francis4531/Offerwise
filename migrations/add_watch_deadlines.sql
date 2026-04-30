-- Add escrow deadline columns to property_watches
ALTER TABLE property_watches ADD COLUMN IF NOT EXISTS inspection_contingency_date DATE;
ALTER TABLE property_watches ADD COLUMN IF NOT EXISTS loan_contingency_date DATE;
ALTER TABLE property_watches ADD COLUMN IF NOT EXISTS seller_response_deadline DATE;
ALTER TABLE property_watches ADD COLUMN IF NOT EXISTS appraisal_contingency_date DATE;
ALTER TABLE property_watches ADD COLUMN IF NOT EXISTS repair_completion_deadline DATE;
ALTER TABLE property_watches ADD COLUMN IF NOT EXISTS close_of_escrow_date DATE;
ALTER TABLE property_watches ADD COLUMN IF NOT EXISTS offer_accepted_date DATE;
ALTER TABLE property_watches ADD COLUMN IF NOT EXISTS last_deadline_check_at TIMESTAMP;
