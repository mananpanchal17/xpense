INVESTMENT_TYPES = [
    ("fixed_deposit", "Fixed Deposit"),
    ("recurring_deposit", "Recurring Deposit"),
    ("ppf", "Public Provident Fund"),
    ("epf", "Employees' Provident Fund"),
    ("vpf", "Voluntary Provident Fund"),
    ("government_bond", "Government bond"),
    ("corporate_bond", "Corporate bond"),
    ("nsc", "National Savings Certificate"),
    ("scss", "Senior Citizens' Savings Scheme"),
    ("post_office", "Post Office savings scheme"),
    ("treasury_bill", "Treasury bill"),
    ("mutual_fund", "Mutual fund"),
    ("sip", "SIP"),
    ("stock", "Stock"),
    ("etf", "Exchange-Traded Fund"),
    ("nps", "National Pension System"),
    ("gold", "Gold"),
    ("sovereign_gold_bond", "Sovereign Gold Bond"),
    ("digital_gold", "Digital gold"),
    ("real_estate", "Real-estate investment"),
    ("cryptocurrency", "Cryptocurrency"),
    ("custom_fixed", "Custom fixed-income investment"),
    ("custom_market", "Custom market-linked investment"),
]

FIXED_TYPES = {key for key, _ in INVESTMENT_TYPES[:11] + INVESTMENT_TYPES[21:22]}
MARKET_TYPES = {key for key, _ in INVESTMENT_TYPES[11:21] + INVESTMENT_TYPES[22:23]}
