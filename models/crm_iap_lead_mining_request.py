import json
import logging
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request
from urllib.request import urlopen

from odoo import _, fields, models
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)


class CrmLeadDiscoveryDistrict(models.Model):
    _name = 'crm.lead.discovery.district'
    _description = 'CRM Lead Discovery District'
    _order = 'name'

    name = fields.Char(required=True)
    state_id = fields.Many2one('res.country.state', string='State', required=True, ondelete='cascade')
    country_id = fields.Many2one(related='state_id.country_id', store=True, readonly=True)

    _district_state_unique = models.Constraint(
        'unique(name, state_id)',
        'This district already exists for the selected state.',
    )


class CrmIapLeadMiningRequest(models.Model):
    _inherit = 'crm.iap.lead.mining.request'

    
    source = fields.Selection(
        selection='_get_available_sources',
        string='Source',
        required=True,
        default='odoo',
    )

    def _get_available_sources(self):
        config = self.env['ir.config_parameter'].sudo()

        sources = [('odoo', 'Odoo')]

        providers = [
            ('geoapify', 'Geoapify Places API', 'GEOAPIFY_API_KEY',
             'crm_lead_discovery.geoapify_api_key'),
            ('apify', 'Apify Google Maps', 'APIFY_API_TOKEN',
             'crm_lead_discovery.apify_api_token'),
            ('foursquare', 'Foursquare Places API', 'FOURSQUARE_API_KEY',
             'crm_lead_discovery.foursquare_api_key'),
            ('tomtom', 'TomTom Places API', 'TOMTOM_API_KEY',
             'crm_lead_discovery.tomtom_api_key'),
        ]

        for source, label, env_name, config_name in providers:
            api_key = (
                os.environ.get(env_name, '').strip()
                or config.get_param(config_name, '').strip()
            )
            if api_key:
                sources.append((source, label))

        return sources
    def _apify_api_request(self, path, payload=None, timeout=45):
        """Call Apify from the server. The token is only sent as an auth header."""
        token = (
            os.environ.get('APIFY_API_TOKEN')
            or self.env['ir.config_parameter'].sudo().get_param(
                'crm_lead_discovery.apify_api_token'
            )
        )
        if not token:
            raise UserError(_(
                'Apify is not configured. An administrator must add an Apify API Token in '
                'Settings → CRM → Lead Generation.'
            ))
        request = Request(
            'https://api.apify.com/v2/%s' % path.lstrip('/'),
            data=json.dumps(payload).encode('utf-8') if payload is not None else None,
            headers={
                'Authorization': 'Bearer %s' % token,
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'User-Agent': 'OdooCRMLeadDiscovery/19.0',
            },
            method='POST' if payload is not None else 'GET',
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read()
                return json.loads(body.decode('utf-8')) if body else {}
        except HTTPError as error:
            if error.code in (401, 403):
                raise UserError(_('Apify rejected the API token or it lacks permission to run this Actor. Check the token in Settings.')) from None
            if error.code == 429:
                raise UserError(_('Apify request limit reached. Try again later.')) from None
            if error.code in (408, 504):
                raise UserError(_('Apify timed out while processing the search. Try again with a smaller maximum.')) from None
            _logger.warning('Apify API returned HTTP %s.', error.code)
            raise UserError(_('Apify returned an HTTP %s error while searching Google Maps.') % error.code) from None
        except (URLError, TimeoutError):
            raise UserError(_('Could not connect to Apify or the request timed out. Check the Odoo server connection and try again.')) from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise UserError(_('Apify returned an invalid response. Try again later.')) from None

    def _apify_google_maps_items(self):
        self.ensure_one()
        if not self.lead_number or self.lead_number < 1:
            raise UserError(_('Enter a maximum number of results greater than zero.'))
        if not self.industry_ids:
            raise UserError(_('Choose at least one industry.'))
        if len(self.country_ids) != 1 or len(self.state_ids) > 1:
            raise UserError(_('For Apify Google Maps, select one country and at most one state.'))
        country = self.country_ids[:1]
        state = self.state_ids[:1]
        if not country:
            raise UserError(_('Select a country for the Apify Google Maps search.'))
        search_query = ', '.join(self.industry_ids.mapped('name'))
        location_query = ', '.join(part for part in ((state.name if state else ''), country.name) if part)

        # These are the Actor's documented input fields. In particular, the
        # Actor's own per-search cap bounds the run before it starts.
        run_response = self._apify_api_request(
            'acts/compass~crawler-google-places/runs?waitForFinish=60&timeout=240',
            {
                'searchStringsArray': [search_query],
                'locationQuery': location_query,
                'maxCrawledPlacesPerSearch': self.lead_number,
                'language': 'en',
                'scrapePlaceDetailPage': False,
            },
            timeout=75,
        )
        run = run_response.get('data') or run_response
        run_id = run.get('id')
        if not run_id:
            raise UserError(_('Apify did not return a run ID for the Google Maps search.'))
        deadline = time.monotonic() + 240
        while run.get('status') not in ('SUCCEEDED', 'FAILED', 'ABORTED', 'TIMED-OUT'):
            if time.monotonic() >= deadline:
                raise UserError(_('Apify Google Maps search exceeded the 5-minute wait limit. Check the run in Apify and try again.'))
            time.sleep(3)
            run_response = self._apify_api_request('actor-runs/%s' % run_id, timeout=30)
            run = run_response.get('data') or run_response
        if run.get('status') != 'SUCCEEDED':
            status = run.get('status') or 'unknown'
            raise UserError(_('The Apify Google Maps Actor run did not succeed (status: %s).') % status)
        dataset_id = (run.get('defaultDatasetId') or (run.get('defaultDataset') or {}).get('id'))
        if not dataset_id:
            raise UserError(_('The Apify Actor run succeeded but did not provide a result dataset.'))
        items = self._apify_api_request(
            'datasets/%s/items?clean=true&format=json&limit=%s' % (dataset_id, self.lead_number),
            timeout=45,
        )
        if not isinstance(items, list):
            raise UserError(_('Apify returned an unexpected Google Maps dataset format.'))
        return items[:self.lead_number]

    @staticmethod
    def _apify_place_id(item):
        place_id = item.get('placeId') or item.get('place_id')
        if place_id:
            return str(place_id)
        maps_url = item.get('url') or item.get('googleMapsUrl') or ''
        return (parse_qs(urlparse(maps_url).query).get('query_place_id') or [False])[0]

    def action_generate_apify_leads(self):
        self.ensure_one()
        items = self._apify_google_maps_items()
        Lead = self.env['crm.lead'].sudo()
        country = self.country_ids[:1]
        values = []
        seen_ids = set()
        for item in items:
            name = item.get('title') or item.get('name')
            if not name:
                continue
            place_id = self._apify_place_id(item)
            address = item.get('address') or False
            if place_id and (place_id in seen_ids or Lead.search([('google_maps_place_id', '=', place_id)], limit=1)):
                continue
            if place_id:
                seen_ids.add(place_id)
            elif Lead.search([('name', '=ilike', name), ('street', '=ilike', address or '')], limit=1):
                continue
            description = _('Discovered automatically through Apify Google Maps Actor.')
            categories = item.get('categories') or []
            if isinstance(categories, str):
                categories = [categories]
            category = item.get('categoryName') or (', '.join(categories) or False)
            maps_url = item.get('url') or item.get('googleMapsUrl') or False
            if category:
                description += _('\nBusiness category: %s') % category
            if maps_url:
                description += _('\nGoogle Maps: %s') % maps_url
            values.append({
                'name': name,
                'partner_name': name,
                'type': self.lead_type or 'lead',
                'street': address,
                'country_id': country.id if country else False,
                'phone': item.get('phone') or False,
                'website': item.get('website') or False,
                'business_category': category,
                'google_maps_place_id': place_id or False,
                'google_maps_url': maps_url,
                'team_id': self.team_id.id,
                'user_id': self.user_id.id,
                'tag_ids': [(6, 0, self.tag_ids.ids)],
                'lead_mining_request_id': self.id,
                'description': description,
            })
        if not values:
            raise UserError(_('Apify Google Maps returned no new usable businesses for the selected filters and location.'))
        leads = self.env['crm.lead'].create(values)
        if self.name == _('New'):
            self.name = self.env['ir.sequence'].next_by_code('crm.iap.lead.mining.request') or _('Apify Google Maps Search')
        self.lead_ids = [(6, 0, leads.ids)]
        self.lead_number = len(leads)
        self.state = 'done'
        if self.lead_type == 'opportunity':
            return self.action_get_opportunity_action()
        return self.action_get_lead_action()

    def _geoapify_categories(self):
        """Translate the shared Odoo Industries filter into Geoapify place categories."""
        self.ensure_one()
        if not self.industry_ids:
            raise UserError(_('Choose at least one industry.'))
        category_map = (
            (('health', 'medical', 'pharma', 'biotech'), ('healthcare',)),
            (('education', 'school', 'university'), ('education.school',)),
            (('food', 'beverage', 'tobacco', 'restaurant'), ('catering.restaurant',)),
            (('hotel', 'accommodation', 'travel', 'leisure'), ('accommodation.hotel',)),
            (('industrial', 'capital goods', 'materials', 'construction', 'energy', 'utilities'), ('building.industrial',)),
            (('bank', 'insurance', 'financial'), ('office.company',)),
            (('retail', 'consumer', 'services', 'real estate', 'media', 'software', 'technology', 'telecommunication', 'transport'), ('commercial', 'office.company')),
        )
        categories = set()
        for industry in self.industry_ids:
            label = industry.name.casefold()
            mapped = False
            for keywords, values in category_map:
                if any(keyword in label for keyword in keywords):
                    categories.update(values)
                    mapped = True
                    break
            if not mapped:
                categories.update(('commercial', 'office.company'))
        return ','.join(sorted(categories))

    def _geoapify_request(self, endpoint, params, timeout=25):
        """Make a Geoapify request without logging or exposing the API key."""
        key = (
            os.environ.get('GEOAPIFY_API_KEY')
            or self.env['ir.config_parameter'].sudo().get_param(
                'crm_lead_discovery.geoapify_api_key'
            )
        )
        if not key:
            raise UserError(_(
                'Geoapify Places API is not configured yet. An administrator must add the API key in '
                'Settings → CRM → Geoapify Places API.'
            ))
        url = '%s?%s' % (endpoint, urlencode({**params, 'apiKey': key}))
        try:
            with urlopen(url, timeout=timeout) as response:
                return json.loads(response.read().decode('utf-8'))
        except HTTPError as error:
            if error.code in (401, 403):
                raise UserError(_('Geoapify rejected the API key. Check the key and Places API access in Geoapify.')) from None
            if error.code == 429:
                raise UserError(_('Geoapify request limit reached. Please try again later.')) from None
            raise UserError(_('Geoapify could not complete the search (HTTP %s).') % error.code) from None
        except (URLError, TimeoutError, json.JSONDecodeError):
            raise UserError(_('Could not connect to Geoapify or read its response. Check the server internet connection and try again.')) from None

    def _geoapify_area_place_id(self):
        self.ensure_one()
        country = self.country_ids[:1]
        state = self.state_ids[:1]
        if not country:
            raise UserError(_('Select a country for the Geoapify search.'))
        if len(self.country_ids) != 1 or len(self.state_ids) > 1:
            raise UserError(_('For Geoapify, select one country and at most one state.'))
        location = [state.name if state else None, country.name]
        query = ', '.join(part for part in location if part)
        result = self._geoapify_request(
            'https://api.geoapify.com/v1/geocode/search',
            {'text': query, 'format': 'json', 'limit': 5},
        )
        results = result.get('results', [])
        country_code = (country.code or '').lower()
        state_name = (state.name or '').casefold() if state else ''
        for place in results:
            props = place.get('properties', place)
            if (props.get('country_code') or '').lower() != country_code:
                continue
            if state and state_name not in (props.get('state') or '').casefold():
                continue
            place_id = props.get('place_id')
            if place_id:
                return place_id
        raise UserError(_('Geoapify could not find that country/state. Check the selected location and try again.'))

    @staticmethod
    def _geoapify_property(properties, *keys):
        for key in keys:
            value = properties.get(key)
            if value:
                return value
        raw = (properties.get('datasource') or {}).get('raw') or {}
        contact = properties.get('contact') or {}
        for key in keys:
            value = contact.get(key) or raw.get(key)
            if value:
                return value
        return False

    def _geoapify_lead_values(self, feature, country, state):
        properties = feature.get('properties') or {}
        name = properties.get('name') or properties.get('address_line1')
        place_id = properties.get('place_id')
        if not name or not place_id:
            return False
        phone = self._geoapify_property(properties, 'phone', 'contact:phone')
        website = self._geoapify_property(properties, 'website', 'contact:website')
        email = self._geoapify_property(properties, 'email', 'contact:email')
        # Avoid duplicate creation even if Geoapify returns a place ID already imported before.
        if self.env['crm.lead'].sudo().search([('geoapify_place_id', '=', place_id)], limit=1):
            return False
        city = properties.get('city') or properties.get('town') or properties.get('village')
        duplicate_domain = [('name', '=ilike', name)]
        if city:
            duplicate_domain.append(('city', '=ilike', city))
        if self.env['crm.lead'].sudo().search(duplicate_domain, limit=1):
            return False
        country_id = self.env['res.country'].search([('code', '=', (properties.get('country_code') or '').upper())], limit=1) or country
        state_id = False
        if properties.get('state'):
            state_id = self.env['res.country.state'].search([
                ('country_id', '=', country_id.id), ('name', '=ilike', properties['state']),
            ], limit=1)
        state_id = state_id or state
        description = _(
            'Discovered automatically through Geoapify Places API.\n'
            'Data attribution: © OpenStreetMap contributors · Powered by Geoapify.\n'
            'Place ID: %(place_id)s', place_id=place_id,
        )
        return {
            'name': name,
            'partner_name': name,
            'type': self.lead_type or 'lead',
            'street': properties.get('address_line1') or properties.get('street') or properties.get('formatted'),
            'street2': properties.get('address_line2'),
            'city': city,
            'zip': properties.get('postcode'),
            'state_id': state_id.id if state_id else False,
            'country_id': country_id.id,
            'phone': phone,
            'email_from': email,
            'website': website,
            'team_id': self.team_id.id,
            'user_id': self.user_id.id,
            'tag_ids': [(6, 0, self.tag_ids.ids)],
            'lead_mining_request_id': self.id,
            'geoapify_place_id': place_id,
            'description': description,
        }

    def action_generate_geoapify_leads(self):
        self.ensure_one()
        categories = self._geoapify_categories()
        if not self.lead_number or self.lead_number < 1:
            raise UserError(_('Enter a number of leads greater than zero.'))
        country = self.country_ids[:1]
        state = self.state_ids[:1]
        area_place_id = self._geoapify_area_place_id()
        values = []
        seen_ids = set()
        page_limit = 20
        lang = self.env.lang.split('_')[0] if self.env.lang else 'en'
        # Page through results when earlier results are already in CRM, while
        # stopping as soon as the requested maximum of new leads is reached.
        for offset in range(0, 500, page_limit):
            result = self._geoapify_request(
                'https://api.geoapify.com/v2/places',
                {
                    'categories': categories,
                    'filter': 'place:%s' % area_place_id,
                    'limit': page_limit,
                    'offset': offset,
                    'lang': lang,
                },
            )
            features = result.get('features') or []
            for feature in features:
                lead_values = self._geoapify_lead_values(feature, country, state)
                if lead_values and lead_values['geoapify_place_id'] not in seen_ids:
                    seen_ids.add(lead_values['geoapify_place_id'])
                    values.append(lead_values)
                if len(values) >= self.lead_number:
                    break
            if len(values) >= self.lead_number or len(features) < page_limit:
                break
        if not values:
            raise UserError(_(
                'Geoapify did not return any new, usable businesses for the selected industries and location. '
                'Broaden your filters and try again.',
            ))
        leads = self.env['crm.lead'].create(values)
        if self.name == _('New'):
            self.name = self.env['ir.sequence'].next_by_code('crm.iap.lead.mining.request') or _('Geoapify Search')
        self.lead_ids = [(6, 0, leads.ids)]
        self.lead_number = len(leads)
        self.state = 'done'
        if self.lead_type == 'opportunity':
            return self.action_get_opportunity_action()
        return self.action_get_lead_action()

    @staticmethod
    def _overpass_escape(value):
        return (value or '').replace('\\', '\\\\').replace('"', '\\"')

    def _overpass_area_query(self):
        self.ensure_one()
        country = self.country_ids[:1]
        state = self.state_ids[:1]
        if not country:
            raise UserError(_('Select a country for the OpenStreetMap search.'))
        if len(self.country_ids) != 1 or len(self.state_ids) > 1:
            raise UserError(_('For OpenStreetMap, select one country and at most one state.'))
        country_code = self._overpass_escape(country.code.upper())
        if state:
            state_name = self._overpass_escape(state.name)
            return (
                'area["ISO3166-1"="%s"]["admin_level"="2"]->.countryArea;\n'
                'rel(area.countryArea)["boundary"="administrative"]["name"="%s"];\n'
                'map_to_area->.searchArea;\n'
            ) % (country_code, state_name)
        return 'area["ISO3166-1"="%s"]["admin_level"="2"]->.searchArea;\n' % country_code

    def _overpass_industry_filters(self):
        """Translate the shared Odoo Industries selection to OSM business tags."""
        self.ensure_one()
        if not self.industry_ids:
            raise UserError(_('Choose at least one industry.'))
        filter_map = (
            (('health', 'medical', 'pharma', 'biotech'), {
                'healthcare': None,
                'amenity': ('doctors', 'clinic', 'hospital', 'dentist', 'pharmacy', 'veterinary'),
            }),
            (('education', 'school', 'university'), {'amenity': ('school', 'college', 'university', 'kindergarten')}),
            (('food', 'beverage', 'tobacco', 'restaurant'), {'amenity': ('restaurant', 'cafe', 'fast_food', 'bar')}),
            (('hotel', 'accommodation', 'travel', 'leisure'), {
                'tourism': ('hotel', 'motel', 'guest_house', 'hostel', 'travel_agency'),
            }),
            (('bank', 'insurance', 'financial'), {
                'amenity': ('bank', 'bureau_de_change'), 'office': ('financial', 'insurance'),
            }),
            (('retail', 'consumer', 'shopping'), {'shop': None}),
            (('real estate', 'property'), {'office': ('estate_agent',)}),
            (('transport', 'logistics'), {'office': ('logistics', 'transport'), 'amenity': ('taxi',)}),
            (('industrial', 'capital goods', 'materials', 'construction', 'energy', 'utilities'), {
                'office': ('company',), 'craft': None,
            }),
            (('software', 'technology', 'telecommunication', 'media', 'professional', 'services'), {
                'office': ('company', 'it', 'telecommunication', 'consulting'),
            }),
        )
        filters = {}
        for industry in self.industry_ids:
            label = industry.name.casefold()
            mapped = False
            for keywords, selected_filters in filter_map:
                if any(keyword in label for keyword in keywords):
                    mapped = True
                    for key, values in selected_filters.items():
                        if values is None:
                            filters[key] = None
                        elif key not in filters:
                            filters[key] = set(values)
                        elif filters[key] is not None:
                            filters[key].update(values)
                    break
            if not mapped:
                filters['office'] = None
                filters['shop'] = None
                filters['craft'] = None
        return filters

    def _overpass_query(self):
        self.ensure_one()
        filters = self._overpass_industry_filters()
        area_query = self._overpass_area_query()
        output_limit = min(max(self.lead_number * 3, self.lead_number), 500)
        selectors = []
        for key, values in sorted(filters.items()):
            if values is None:
                tag_filter = '["%s"]' % key
            else:
                alternatives = '|'.join(re.escape(value) for value in sorted(values))
                tag_filter = '["%s"~"^(%s)$"]' % (key, alternatives)
            selectors.append('  nwr(area.searchArea)["name"]%s;' % tag_filter)
        return (
            '[out:json][timeout:25];\n'
            '%s'
            '(\n%s\n)->.businesses;\n'
            '.businesses out center tags qt %s;\n'
        ) % (area_query, '\n'.join(selectors), output_limit)

    def _overpass_request(self, query):
        request = Request(
            'https://overpass-api.de/api/interpreter',
            data=urlencode({'data': query}).encode('utf-8'),
            headers={
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'User-Agent': 'OdooCRMLeadDiscovery/19.0 (user initiated Overpass search)',
            },
            method='POST',
        )
        try:
            with urlopen(request, timeout=45) as response:
                return json.loads(response.read().decode('utf-8'))
        except HTTPError as error:
            if error.code in (429, 504):
                raise UserError(_('OpenStreetMap search is busy. Wait a moment and try again.')) from None
            if error.code in (400, 413):
                raise UserError(_('OpenStreetMap could not process this search. Try a smaller area or fewer industries.')) from None
            raise UserError(_('OpenStreetMap Overpass returned HTTP %s.') % error.code) from None
        except (URLError, TimeoutError, json.JSONDecodeError):
            raise UserError(_('Could not connect to OpenStreetMap Overpass. Try again later.')) from None

    @staticmethod
    def _osm_tag(tags, *keys):
        for key in keys:
            if tags.get(key):
                return tags[key]
        return False

    def _osm_lead_values(self, element, country, state):
        tags = element.get('tags') or {}
        name = tags.get('name') or tags.get('brand')
        element_type = element.get('type')
        element_id = element.get('id')
        if not name or not element_type or not element_id:
            return False
        osm_element_id = '%s/%s' % (element_type, element_id)
        if self.env['crm.lead'].sudo().search([('osm_element_id', '=', osm_element_id)], limit=1):
            return False
        city = self._osm_tag(tags, 'addr:city', 'addr:town', 'addr:village', 'addr:suburb')
        duplicate_domain = [('name', '=ilike', name)]
        if city:
            duplicate_domain.append(('city', '=ilike', city))
        if self.env['crm.lead'].sudo().search(duplicate_domain, limit=1):
            return False
        street = self._osm_tag(tags, 'addr:street')
        house_number = self._osm_tag(tags, 'addr:housenumber')
        if street and house_number:
            street = '%s %s' % (house_number, street)
        elif not street:
            street = self._osm_tag(tags, 'addr:full')
        description = _(
            'Discovered automatically through OpenStreetMap Overpass.\n'
            'Data attribution: © OpenStreetMap contributors (https://www.openstreetmap.org/copyright).\n'
            'OSM element: %(element_id)s', element_id=osm_element_id,
        )
        return {
            'name': name,
            'partner_name': name,
            'type': self.lead_type or 'lead',
            'street': street,
            'city': city,
            'zip': self._osm_tag(tags, 'addr:postcode'),
            'state_id': state.id if state else False,
            'country_id': country.id,
            'phone': self._osm_tag(tags, 'contact:phone', 'phone'),
            'email_from': self._osm_tag(tags, 'contact:email', 'email'),
            'website': self._osm_tag(tags, 'contact:website', 'website'),
            'team_id': self.team_id.id,
            'user_id': self.user_id.id,
            'tag_ids': [(6, 0, self.tag_ids.ids)],
            'lead_mining_request_id': self.id,
            'osm_element_id': osm_element_id,
            'description': description,
        }

    def action_generate_openstreetmap_leads(self):
        self.ensure_one()
        if not self.lead_number or self.lead_number < 1:
            raise UserError(_('Enter a number of leads greater than zero.'))
        country = self.country_ids[:1]
        state = self.state_ids[:1]
        result = self._overpass_request(self._overpass_query())
        values = []
        seen_ids = set()
        for element in result.get('elements') or []:
            lead_values = self._osm_lead_values(element, country, state)
            if lead_values and lead_values['osm_element_id'] not in seen_ids:
                seen_ids.add(lead_values['osm_element_id'])
                values.append(lead_values)
            if len(values) >= self.lead_number:
                break
        if not values:
            raise UserError(_(
                'OpenStreetMap returned no new named businesses for the selected industries and location. '
                'Try a broader location or different industries.',
            ))
        leads = self.env['crm.lead'].create(values)
        if self.name == _('New'):
            self.name = self.env['ir.sequence'].next_by_code('crm.iap.lead.mining.request') or _('OpenStreetMap Search')
        self.lead_ids = [(6, 0, leads.ids)]
        self.lead_number = len(leads)
        self.state = 'done'
        if self.lead_type == 'opportunity':
            return self.action_get_opportunity_action()
        return self.action_get_lead_action()

    def _foursquare_request(self, params):
        key = (
            os.environ.get('FOURSQUARE_API_KEY')
            or self.env['ir.config_parameter'].sudo().get_param(
                'crm_lead_discovery.foursquare_api_key'
            )
        )
        if not key:
            raise UserError(_(
                'Foursquare Places API is not configured. An administrator must add a Service API Key in '
                'Settings → CRM → Lead Generation.'
            ))
        url = 'https://places-api.foursquare.com/places/search?%s' % urlencode(params)
        request = Request(url, headers={
            'Authorization': 'Bearer %s' % key,
            'Accept': 'application/json',
            'X-Places-Api-Version': '2025-06-17',
            'User-Agent': 'OdooCRMLeadDiscovery/19.0',
        })
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode('utf-8'))
        except HTTPError as error:
            if error.code in (401, 403):
                raise UserError(_('Foursquare rejected the Service API Key or its Places API access. Check the key in Settings.')) from None
            if error.code == 429:
                raise UserError(_('Foursquare request limit reached. Check your plan usage and try again later.')) from None
            raise UserError(_('Foursquare Places API returned HTTP %s.') % error.code) from None
        except (URLError, TimeoutError, json.JSONDecodeError):
            raise UserError(_('Could not connect to Foursquare Places API. Check the server connection and try again.')) from None

    def _foursquare_lead_values(self, place, country, state):
        place_id = place.get('fsq_place_id')
        name = place.get('name')
        if not place_id or not name:
            return False
        lead_model = self.env['crm.lead'].sudo()
        if lead_model.search([('foursquare_place_id', '=', place_id)], limit=1):
            return False
        location = place.get('location') or {}
        city = location.get('locality') or location.get('region')
        duplicate_domain = [('name', '=ilike', name)]
        if city:
            duplicate_domain.append(('city', '=ilike', city))
        if lead_model.search(duplicate_domain, limit=1):
            return False
        country_id = country
        if location.get('country'):
            country_id = self.env['res.country'].search([('code', '=ilike', location['country'])], limit=1) or country
        state_id = state
        if location.get('region'):
            state_id = self.env['res.country.state'].search([
                '&', ('country_id', '=', country_id.id), '|',
                ('name', '=ilike', location['region']), ('code', '=ilike', location['region']),
            ], limit=1) or state
        return {
            'name': name,
            'partner_name': name,
            'type': self.lead_type or 'lead',
            'street': location.get('address') or location.get('formatted_address'),
            'city': city,
            'zip': location.get('postcode'),
            'state_id': state_id.id if state_id else False,
            'country_id': country_id.id if country_id else False,
            'phone': place.get('tel'),
            'email_from': place.get('email'),
            'website': place.get('website'),
            'team_id': self.team_id.id,
            'user_id': self.user_id.id,
            'tag_ids': [(6, 0, self.tag_ids.ids)],
            'lead_mining_request_id': self.id,
            'foursquare_place_id': place_id,
            'description': _('Discovered automatically through Foursquare Places API. Place ID: %s') % place_id,
        }

    def action_generate_foursquare_leads(self):
        self.ensure_one()
        if not self.lead_number or self.lead_number < 1:
            raise UserError(_('Enter a number of leads greater than zero.'))
        if not self.industry_ids:
            raise UserError(_('Choose at least one industry.'))
        if len(self.country_ids) != 1 or len(self.state_ids) > 1:
            raise UserError(_('For Foursquare, select one country and at most one state.'))
        country = self.country_ids[:1]
        state = self.state_ids[:1]
        if not country:
            raise UserError(_('Select a country for the Foursquare search.'))
        near = ', '.join(part for part in ((state.name if state else ''), country.name) if part)
        values = []
        seen_ids = set()
        for industry in self.industry_ids:
            remaining = self.lead_number - len(values)
            if remaining <= 0:
                break
            result = self._foursquare_request({
                'query': industry.name,
                'near': near,
                'limit': min(50, remaining),
                'sort': 'RELEVANCE',
                'fields': 'fsq_place_id,name,location,categories,tel,email,website',
            })
            for place in result.get('results') or []:
                lead_values = self._foursquare_lead_values(place, country, state)
                if lead_values and lead_values['foursquare_place_id'] not in seen_ids:
                    seen_ids.add(lead_values['foursquare_place_id'])
                    values.append(lead_values)
                if len(values) >= self.lead_number:
                    break
        if not values:
            raise UserError(_('Foursquare returned no new usable places for the selected industries and location.'))
        leads = self.env['crm.lead'].create(values)
        if self.name == _('New'):
            self.name = self.env['ir.sequence'].next_by_code('crm.iap.lead.mining.request') or _('Foursquare Search')
        self.lead_ids = [(6, 0, leads.ids)]
        self.lead_number = len(leads)
        self.state = 'done'
        if self.lead_type == 'opportunity':
            return self.action_get_opportunity_action()
        return self.action_get_lead_action()

    def _tomtom_request(self, payload):
        key = (
            os.environ.get('TOMTOM_API_KEY')
            or self.env['ir.config_parameter'].sudo().get_param(
                'crm_lead_discovery.tomtom_api_key'
            )
        )
        if not key:
            raise UserError(_(
                'TomTom Places API is not configured. An administrator must add a TomTom Places API key in '
                'Settings → CRM → Lead Generation.'
            ))
        request = Request(
            'https://api.tomtom.com/maps/orbis/places/discover',
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'TomTom-Api-Key': key,
                'TomTom-Api-Version': '3',
                'Attributes': 'results(id,type,title,address,subtitles,poiTypes,contacts(phones,websites))',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'User-Agent': 'OdooCRMLeadDiscovery/19.0',
            },
            method='POST',
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode('utf-8'))
        except HTTPError as error:
            # TomTom returns a structured detailedError payload. Surface its
            # safe, actionable message so invalid API entitlement/request
            # parameters do not look like a network outage in the UI.
            try:
                error_payload = json.loads(error.read().decode('utf-8'))
                details = error_payload.get('detailedError') or {}
                provider_message = details.get('message') or details.get('code')
                if not provider_message and details.get('details'):
                    provider_message = details['details'][0].get('message')
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, IndexError, TypeError):
                provider_message = None
            if error.code in (401, 403):
                raise UserError(_(
                    'TomTom rejected the API key or it does not have Places Search API access. '
                    'Check the key and Places Search entitlement in TomTom. %(detail)s',
                    detail=provider_message or '',
                )) from None
            if error.code == 429:
                raise UserError(_('TomTom Places request limit reached. Check your plan usage and try again later.')) from None
            if error.code == 400:
                raise UserError(_(
                    'TomTom Places could not process this search (HTTP 400). %(detail)s',
                    detail=provider_message or 'Check the industry and location filters.',
                )) from None
            raise UserError(_(
                'TomTom Places Search returned HTTP %(code)s. %(detail)s',
                code=error.code, detail=provider_message or '',
            )) from None
        except URLError as error:
            reason = error.reason
            # The API key is sent only in a header, never in the URL or diagnostic message.
            _logger.warning('TomTom Places connection failed (%s): %s', type(reason).__name__, reason)
            raise UserError(_(
                'Odoo could not reach TomTom Places Search (%(error_type)s: %(reason)s). '
                'Check outbound DNS, HTTPS access, and the Odoo service proxy settings.',
                error_type=type(reason).__name__, reason=str(reason)[:180],
            )) from None
        except TimeoutError:
            _logger.warning('TomTom Places request timed out after 30 seconds.')
            raise UserError(_('TomTom Places Search timed out. Check the Odoo server connection and try again.')) from None
        except json.JSONDecodeError:
            _logger.warning('TomTom Places returned a non-JSON response.')
            raise UserError(_('TomTom Places returned an invalid response. Check the API entitlement and try again.')) from None

    def _tomtom_lead_values(self, place, country, state):
        place_id = place.get('id')
        name = place.get('title')
        if not place_id or not name or place.get('type') != 'poi':
            return False
        lead_model = self.env['crm.lead'].sudo()
        if lead_model.search([('tomtom_place_id', '=', place_id)], limit=1):
            return False

        address = place.get('address') or {}
        remote_country_code = (address.get('countryCodeIso2') or '').upper()
        if remote_country_code and country.code and remote_country_code != country.code.upper():
            return False
        remote_state = address.get('countrySubdivision') or ''
        if state and remote_state and remote_state.casefold() not in (state.name or '').casefold() and remote_state.casefold() != (state.code or '').casefold():
            return False

        city = address.get('municipality') or address.get('municipalitySubdivision')
        duplicate_domain = [('name', '=ilike', name)]
        if city:
            duplicate_domain.append(('city', '=ilike', city))
        if lead_model.search(duplicate_domain, limit=1):
            return False

        country_id = country
        if remote_country_code:
            country_id = self.env['res.country'].search([('code', '=', remote_country_code)], limit=1) or country
        state_id = state
        if remote_state and country_id:
            state_id = self.env['res.country.state'].search([
                '&', ('country_id', '=', country_id.id), '|',
                ('name', '=ilike', remote_state), ('code', '=ilike', remote_state),
            ], limit=1) or state

        contacts = place.get('contacts') or []
        phone = next((value for contact in contacts for value in (contact.get('phones') or []) if value), False)
        website = next((value for contact in contacts for value in (contact.get('websites') or []) if value), False)
        street = ' '.join(part for part in (address.get('street'), address.get('houseNumber')) if part)
        if not street:
            subtitles = place.get('subtitles') or []
            street = subtitles[0] if subtitles else False
        return {
            'name': name,
            'partner_name': name,
            'type': self.lead_type or 'lead',
            'street': street,
            'city': city,
            'zip': address.get('postalCode'),
            'state_id': state_id.id if state_id else False,
            'country_id': country_id.id if country_id else False,
            'phone': phone,
            'website': website,
            'team_id': self.team_id.id,
            'user_id': self.user_id.id,
            'tag_ids': [(6, 0, self.tag_ids.ids)],
            'lead_mining_request_id': self.id,
            'tomtom_place_id': place_id,
            'description': _('Discovered automatically through TomTom Places Search API. Place ID: %s') % place_id,
        }

    def action_generate_tomtom_leads(self):
        self.ensure_one()
        if not self.lead_number or self.lead_number < 1:
            raise UserError(_('Enter a number of leads greater than zero.'))
        if not self.industry_ids:
            raise UserError(_('Choose at least one industry.'))
        if len(self.country_ids) != 1 or len(self.state_ids) > 1:
            raise UserError(_('For TomTom, select one country and at most one state.'))
        country = self.country_ids[:1]
        state = self.state_ids[:1]
        if not country:
            raise UserError(_('Select a country for the TomTom search.'))

        location = ', '.join(part for part in ((state.name if state else ''), country.name) if part)
        values = []
        seen_ids = set()
        for industry in self.industry_ids:
            remaining = self.lead_number - len(values)
            if remaining <= 0:
                break
            result = self._tomtom_request({
                'query': '%s in %s' % (industry.name, location),
                'maxResults': min(100, remaining),
                'filters': {
                    'types': ['poi'],
                    'countryCodesIso2': [country.code.upper()],
                },
                'geopoliticalView': 'IN' if country.code.upper() == 'IN' else 'Unified',
            })
            for place in result.get('results') or []:
                lead_values = self._tomtom_lead_values(place, country, state)
                if lead_values and lead_values['tomtom_place_id'] not in seen_ids:
                    seen_ids.add(lead_values['tomtom_place_id'])
                    values.append(lead_values)
                if len(values) >= self.lead_number:
                    break
        if not values:
            raise UserError(_('TomTom returned no new usable businesses for the selected industries and location.'))
        leads = self.env['crm.lead'].create(values)
        if self.name == _('New'):
            self.name = self.env['ir.sequence'].next_by_code('crm.iap.lead.mining.request') or _('TomTom Places Search')
        self.lead_ids = [(6, 0, leads.ids)]
        self.lead_number = len(leads)
        self.state = 'done'
        if self.lead_type == 'opportunity':
            return self.action_get_opportunity_action()
        return self.action_get_lead_action()

    def action_submit(self):
        self.ensure_one()
        # Keep the shared popup identical across every source. None of the
        # configured sources applies an employee-count filter.
        self.filter_on_size = False
        # Lead discovery is for businesses only. Keep the legacy selection
        # field hidden in the UI and normalize it before every provider runs.
        self.search_type = 'companies'
        if self.source == 'apify':
            return self.action_generate_apify_leads()
        if self.source == 'geoapify':
            return self.action_generate_geoapify_leads()
        if self.source == 'foursquare':
            return self.action_generate_foursquare_leads()
        if self.source == 'tomtom':
            return self.action_generate_tomtom_leads()
        return super().action_submit()
