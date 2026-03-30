{
    'name': 'Timbre Fiscal',
    'version': '1.0',
    'category': 'Accounting',
    'summary': 'Add Timbre Fiscal to Account Moves',
    'description': """
        This module adds timbre fiscal functionality to account moves.
    """,
    'author': 'Digi-IT',
    'website': 'https://digi-it.com.tn',
    'depends': ['account'],
    'data': [
        'views/res_partner_views.xml',
        'views/account_account_views.xml',
        'views/account_move_views.xml',
        'views/account_tax_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'images': ['static/description/cover.png'],
}