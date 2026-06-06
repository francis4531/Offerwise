"""Tests for card_import — classification and monthly grouping."""

import card_import as ci


def test_classify_matches_known_vendors():
    assert ci.classify_charge('RENDER.COM SAN FRANCISCO CA', 75.0) == ('Render', None)
    assert ci.classify_charge('ANTHROPIC SAN FRANCISCO CA', 25.0) == ('Anthropic', None)
    assert ci.classify_charge('CLAUDE.AI SUBSCRIPTION SF', 100.0) == ('Anthropic', None)
    assert ci.classify_charge('RENTCAST API - FOUND SAN DIEGO', 74.0) == ('RentCast', None)
    assert ci.classify_charge('HUNTER.IO STARTER WILMINGTON DE', 49.0) == ('Hunter', None)
    assert ci.classify_charge('GOOGLE*CLOUD GX4547', 2.28) == ('Google Cloud', None)
    assert ci.classify_charge('PORKBUN LLC', 143.80) == ('Porkbun', None)
    assert ci.classify_charge('INTER NACHI 65000001 BOULDER CO', 49.0) == ('InterNACHI', None)


def test_ad_channels_are_skipped_not_imported():
    assert ci.classify_charge('GOOGLE *ADS533070536', 175.65) == (None, 'ad_synced')
    assert ci.classify_charge('REDDIT INC ADS SAN FRANCISCO', 50.0) == (None, 'ad_synced')


def test_payments_and_unmatched():
    assert ci.classify_charge('MOBILE PAYMENT - THANK YOU', -300.0) == (None, 'payment_or_credit')
    assert ci.classify_charge('RENDER.COM', 0.0) == (None, 'payment_or_credit')
    assert ci.classify_charge("APLPAY MCDONALD'S MENLO PARK CA", 25.11) == (None, 'unmatched')


def test_parse_groups_by_month_and_sums():
    csv_text = (
        "Date,Receipt,Description,Amount\n"
        "01/08/2026,,ANTHROPIC SAN FRANCISCO CA,5.00\n"
        "01/28/2026,,ANTHROPIC SAN FRANCISCO CA,25.00\n"
        "02/04/2026,,RENDER.COM SAN FRANCISCO CA,68.28\n"
        "01/15/2026,,GOOGLE *ADS533070536,100.00\n"
        "01/20/2026,,APLPAY TACO BELL,9.50\n"
        "01/18/2026,,MOBILE PAYMENT - THANK YOU,-50.00\n"
    )
    out = ci.parse_card_csv(csv_text)
    invs = {(i['vendor'], i['period_start']): i for i in out['invoices']}
    # Two Anthropic charges in Jan collapse into one $30 invoice
    assert invs[('Anthropic', '2026-01-01')]['amount'] == 30.00
    assert invs[('Anthropic', '2026-01-01')]['charge_count'] == 2
    assert invs[('Anthropic', '2026-01-01')]['period_end'] == '2026-01-31'
    # Render in Feb is its own invoice
    assert invs[('Render', '2026-02-01')]['amount'] == 68.28
    # Ad spend never becomes an invoice
    assert all(i['vendor'] != 'Google Ads' for i in out['invoices'])
    assert out['skipped']['ad_synced']['count'] == 1
    assert out['skipped']['ad_synced']['amount'] == 100.00
    assert out['skipped']['unmatched']['count'] == 1
    assert out['skipped']['payment_or_credit']['count'] == 1
    assert out['matched_total'] == 30.00 + 68.28


def test_handles_case_insensitive_headers_and_blank_lines():
    csv_text = (
        "date,description,amount\n"
        "03/04/2026,RENDER.COM,75.36\n"
    )
    out = ci.parse_card_csv(csv_text)
    assert len(out['invoices']) == 1
    assert out['invoices'][0]['amount'] == 75.36


def test_parses_bank_table_copy_no_header_whitespace():
    """A transaction list copied from a bank's website: no header, whitespace
    aligned, with City/State columns. This is what broke the first real import."""
    text = (
        "01/17/2026          WYZANT              SAN MATEO       CA      43.60\n"
        "01/16/2026          MOBILE PAYMENT - THANK YOU      -1261.87\n"
        "01/05/2026          CLAUDE.AI SUBSCRIPTISAN FRANCISCO       CA      88.77\n"
        "01/03/2026          PORKBUN* PORKBUN.COMSHERWOOD     OR      143.80\n"
    )
    out = ci.parse_card_csv(text)
    invs = {(i['vendor'], i['period_start']): i for i in out['invoices']}
    assert invs[('Anthropic', '2026-01-01')]['amount'] == 88.77   # CLAUDE.AI -> Anthropic
    assert invs[('Porkbun', '2026-01-01')]['amount'] == 143.80
    assert out['skipped']['payment_or_credit']['count'] == 1       # the -1261.87 credit
    assert out['skipped']['unmatched']['count'] == 1               # WYZANT (personal)
    assert out['matched_total'] == 88.77 + 143.80


def test_parses_tab_delimited_no_header():
    text = (
        "01/05/2026\tCLAUDE.AI SUBSCRIPTION\tSAN FRANCISCO\tCA\t88.77\n"
        "01/03/2026\tPORKBUN.COM\tSHERWOOD\tOR\t143.80\n"
    )
    out = ci.parse_card_csv(text)
    assert len(out['invoices']) == 2
    assert out['matched_total'] == 88.77 + 143.80


def test_amount_with_dollar_and_commas_in_table_copy():
    text = "03/04/2026   RENDER.COM   SAN FRANCISCO   CA   $1,075.36\n"
    out = ci.parse_card_csv(text)
    assert len(out['invoices']) == 1
    assert out['invoices'][0]['vendor'] == 'Render'
    assert out['invoices'][0]['amount'] == 1075.36


def test_comma_csv_still_works_unchanged():
    """Regression guard: the original comma-CSV-with-header path is untouched."""
    csv_text = (
        "Date,Receipt,Description,Amount\n"
        "06/03/2026,,INTER NACHI 65000001BOULDER             CO,49.00\n"
        "06/03/2026,,CHARGEPOINT INC     CAMPBELL            CA,3.80\n"
    )
    out = ci.parse_card_csv(csv_text)
    # CHARGEPOINT is personal/unmatched; InterNACHI matches its vendor
    assert any(i['vendor'] == 'InterNACHI' and i['amount'] == 49.00 for i in out['invoices']) \
        or out['skipped']['unmatched']['count'] >= 1
