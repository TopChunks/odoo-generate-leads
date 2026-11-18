# CRM Lead Discovery â€“ Odoo 19

Generate business leads in Odoo CRM using multiple external data providers.

## Features

- **Odoo IAP** â€“ Existing Odoo lead-generation provider.
- **Geoapify** â€“ Discover businesses using Geoapify Places API.
- **Foursquare** â€“ Search businesses using Foursquare Places API.
- **TomTom** â€“ Discover points of interest using TomTom Places API.
- **Apify Google Maps** â€“ Generate business leads using an Apify Google Maps Actor.

### Lead Management

- Select a lead source from the Generate Leads popup.
- Filter searches using industries and locations.
- Configure the maximum number of results.
- Automatically create CRM leads from provider results.
- Map business information such as name, address, phone number and website.
- Avoid duplicate leads using provider place IDs and business details.

## Requirements

- Odoo 19.0
- CRM module
- CRM Lead Mining (`crm_iap_mine`) module
- Valid API credentials for the external providers you want to use.

## Installation

1. Clone this repository into your Odoo custom addons directory.
2. Make sure the custom addons directory is included in your Odoo `addons_path`.
3. Restart the Odoo server.
4. Open the Apps menu.
5. Update the Apps List if necessary.
6. Search for **Generate Leads Source Selector**.
7. Install the module.

## API Configuration

The module supports two credential configuration methods:

1. Environment variables on the Odoo server.
2. API credentials configured in Odoo Settings â†’ CRM â†’ Lead Generation.

Environment variables take priority over credentials stored in Odoo settings.

| Provider | Environment variable |
|---|---|
| Geoapify | `GEOAPIFY_API_KEY` |
| Foursquare | `FOURSQUARE_API_KEY` |
| TomTom | `TOMTOM_API_KEY` |
| Apify | `APIFY_API_TOKEN` |

Configure only the credentials for the providers you intend to use.

### Important

Environment variables must be available to the Odoo server process itself. Restart the Odoo service after configuring them.

For production deployments, use your operating system's service configuration or a secure secrets manager.

**Never commit actual API keys, tokens, passwords or `.env` files to GitHub.**

## Usage

1. Open Odoo CRM.
2. Navigate to the Generate Leads feature.
3. Select a lead source.
4. Choose the required industries and location.
5. Enter the maximum number of leads.
6. Submit the request.
7. Review the generated leads in CRM.

External providers may return fewer results than requested depending on their API limits, search filters, availability and duplicate detection.

## Security

- API credentials are retrieved on the Odoo server.
- API tokens are not intended to be exposed in browser code.
- Environment variables are preferred for organization-managed deployments.
- Keep API credentials private and follow each provider's usage terms.

## Technologies

- Python
- Odoo 19
- PostgreSQL
- XML
- External REST APIs
- Git and GitHub

## License

LGPL-3

## Author

Local customization - Odoo CRM Lead Discovery project.
