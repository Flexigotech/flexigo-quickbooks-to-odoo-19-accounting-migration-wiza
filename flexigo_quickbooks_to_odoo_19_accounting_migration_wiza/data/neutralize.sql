-- A copy never talks with the identity of the original (receta §9): the QuickBooks app secret and tokens that a
-- connection bought before FlexigoTech Cloud keeps here, and the marks that a connection through FlexigoTech Cloud
-- saved its Intuit app there and authorized it, belong to the original database. The client ID stays (it is public);
-- nothing else changes and no state here triggers a purge.
UPDATE qb_connection
   SET oauth_client_secret_encrypted = NULL,
       oauth_access_token_encrypted = NULL,
       oauth_refresh_token_encrypted = NULL;
-- fgt_saved_at and fgt_connected_on arrive with 1.1.0: a copy neutralized with this code before its -u has no such
-- columns yet, and one failing statement would leave the whole database not neutralized (kb_conector_pasarela_pausas §4)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = 'qb_connection'
                  AND column_name = 'fgt_saved_at') THEN
        UPDATE qb_connection SET fgt_saved_at = NULL, fgt_connected_on = NULL;
    END IF;
END $$;
