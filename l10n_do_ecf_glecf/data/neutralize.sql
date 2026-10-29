-- A copy of the database (a test or staging copy) must never send e-CF to the
-- DGII with the company's real GLECF key.
UPDATE res_company
   SET glecf_api_key = NULL,
       glecf_consent = FALSE;
