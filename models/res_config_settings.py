from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    geoapify_api_key = fields.Char(
        string='Geoapify API Key',
        config_parameter='crm_lead_discovery.geoapify_api_key',
        groups='base.group_system',
    )
    foursquare_api_key = fields.Char(
        string='Foursquare Service API Key',
        config_parameter='crm_lead_discovery.foursquare_api_key',
        groups='base.group_system',
    )
    tomtom_api_key = fields.Char(
        string='TomTom Places API Key',
        config_parameter='crm_lead_discovery.tomtom_api_key',
        groups='base.group_system',
    )
    apify_api_token = fields.Char(
        string='Apify API Token',
        config_parameter='crm_lead_discovery.apify_api_token',
        groups='base.group_system',
    )
