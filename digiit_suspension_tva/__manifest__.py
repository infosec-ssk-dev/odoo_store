# -*- coding: utf-8 -*-
{
    'name': 'Suspension TVA',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Gestion de la suspension TVA, autorisations, bons de commandes visés et déclarations',
    'author': 'Digi-ERP',
    'website': 'https://digi-it.com.tn',
    'depends': [
        'account',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/res_partner_views.xml',
        'views/account_fiscal_position_views.xml',
        'views/autorisation_views.xml',
        'views/account_move_views.xml',
        'views/bon_commande_vise_views.xml',
        'views/declaration_suspension_views.xml',
        'views/menus.xml',
    ],
    'images': ['static/description/cover.png'],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
