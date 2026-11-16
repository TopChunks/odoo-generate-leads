{
    'name': 'Generate Leads Source Selector',
    'version': '19.0.1.25.0',
    'summary': 'Choose a source from the Generate Leads popup',
    'category': 'Sales/CRM',
    'author': 'Local customization',
    'license': 'LGPL-3',
    'depends': ['crm', 'crm_iap_mine'],
    'data': [
        'security/ir.model.access.csv',
        'views/crm_lead_views.xml',
        'views/crm_generate_leads_source.xml',
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
    'application': False,
}
