from odoo import fields, models


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    _google_maps_place_unique = models.Constraint(
        'unique(google_maps_place_id)',
        'A CRM lead for this Google Maps place already exists.',
    )

    geoapify_place_id = fields.Char(
        string='Geoapify Place ID', index=True, copy=False, readonly=True,
    )
    osm_element_id = fields.Char(
        string='OpenStreetMap Element ID', index=True, copy=False, readonly=True,
    )
    foursquare_place_id = fields.Char(
        string='Foursquare Place ID', index=True, copy=False, readonly=True,
    )
    tomtom_place_id = fields.Char(
        string='TomTom Place ID', index=True, copy=False, readonly=True,
    )
    google_maps_place_id = fields.Char(
        string='Google Maps Place ID', index=True, copy=False, readonly=True,
    )
    google_maps_url = fields.Char(
        string='Google Maps URL', copy=False, readonly=True,
    )
    business_category = fields.Char(
        string='Business Category', copy=False, readonly=True,
    )
    apollo_organization_id = fields.Char(
        string='Apollo Organization ID', index=True, copy=False, readonly=True,
    )

    employee_count = fields.Integer(
        string='Company Employee Count',
        help='Enter an employee count from a source your organization is permitted to use. '
             'Geoapify Places and OpenStreetMap do not provide employee counts.',
    )
    industry_id = fields.Many2one(
        'res.partner.industry',
        string='Industry',
        help='Industry recorded for this lead, for filtering in Lead Discovery.',
    )
