import argparse
import click
import json
import os
import re
import requests
import sys

def parse_input_data(data_str):
    try:
        if data_str == '-':
            data_str = sys.stdin.read().strip()
        return json.loads(data_str)
    except json.JSONDecodeError:
        return data_str

def parse_additional_data(unknown_args):
    additional_data = {}
    for i in range(0, len(unknown_args), 2):
        if unknown_args[i].startswith('--'):
            key = unknown_args[i].lstrip('--').replace('-', '_')
            value = unknown_args[i+1]
            additional_data[key] = parse_input_data(value) if value == '-' else parse_input_data(value)
    return additional_data

def construct_request_data(args, unknown_args):
    data = parse_input_data(args.get('data')) if args.get('data') else {}
    additional_data = parse_additional_data(unknown_args)
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                item.update(additional_data)
    elif isinstance(data, dict):
        data.update(additional_data)
    return data

def format_placeholders(obj, values):
    """
    Recursively replace placeholders in all string values of a dict, list, or str.
    """
    if isinstance(obj, str):
        try:
            return obj.format(**values)
        except KeyError:
            return obj  # leave as-is if placeholder not provided
    elif isinstance(obj, dict):
        return {k: format_placeholders(v, values) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [format_placeholders(v, values) for v in obj]
    else:
        return obj

def make_paginated_request(url, headers, params):
    """
    Retrieves all pages of data from an API endpoint that supports pagination.

    Supports two pagination formats:

    1) Page-based (Core BigCommerce):
        meta.pagination.total_pages
        meta.pagination.current_page

    2) Offset-based (B2B Edition):
        meta.pagination.totalCount
        meta.pagination.offset
        meta.pagination.limit

    :param url: The API endpoint URL.
    :param headers: HTTP headers to include in the request.
    :param params: Query parameters to include in the request, excluding pagination parameters.
     :return: A dictionary containing all retrieved data under the key "data". If an error
             occurs during the request, the function returns the error response from the API.
    """
    results = []
    page = 1
    offset = params.get('offset', 0) if params else 0
    limit = params.get('limit', None) if params else None

    while True:
        # Build request parameters depending on pagination mode
        if limit is not None:
            paginated_params = {**params, 'offset': offset} if params else {'offset': offset}
        else:
            paginated_params = {**params, 'page': page} if params else {'page': page}

        response = requests.get(url, headers=headers, params=paginated_params)
        if response.status_code != 200:
            return response.json()

        json_response = response.json()
        results.extend(json_response.get('data', []))

        pagination = json_response.get('meta', {}).get('pagination', {})

        # ----- Page-based mode -----
        if 'total_pages' in pagination:
            current_page = pagination.get('current_page', page)
            total_pages = pagination.get('total_pages', current_page)
            if current_page >= total_pages:
                break
            page += 1

        # ----- Offset-based mode -----
        elif 'totalCount' in pagination and 'limit' in pagination:
            total = pagination.get('totalCount', 0)
            limit = pagination.get('limit', limit or 0)
            offset = pagination.get('offset', offset)

            next_offset = offset + limit
            if next_offset >= total:
                break
            offset = next_offset

        # ----- No recognizable pagination -----
        else:
            break

    return {"data": results}

def make_chunked_request(method, url, headers, data, limit):
    """
    Handles POST/PUT requests in chunks when the data array exceeds the limit.

    :param method: HTTP method (POST or PUT).
    :param url: The API endpoint URL.
    :param headers: HTTP headers to include in the request.
    :param data: The data array to be sent.
    :param limit: Maximum number of items per request.
    :return: Combined response from all chunked requests.
    """
    if not isinstance(data, list):
        raise ValueError("Data must be a list for chunked requests.")

    combined_data = []  # To store all results from chunked requests

    results = []
    for i in range(0, len(data), limit):
        chunk = data[i:i + limit]
        chunk_response = requests.request(
            method,
            url,
            headers=headers,
            json=chunk
        )
        if chunk_response.status_code not in [200, 201]:
            # Return the error response if any chunk fails
            return chunk_response.json()

        json_response = chunk_response.json()
        results.extend(json_response.get('data', []))
    return {"data": results}

def make_request(config):
    """
    Perform an HTTP request with the given configuration.

    :param config: Dictionary containing the request configuration.
    :return: JSON response or error.
    """
    url = config.get('url')

    headers = config.get('headers', {}).copy()
    if config.get('files'):
        headers.pop('Content-Type', None)

    if config.get('all_pages') and config.get('method') == 'GET':
        return make_paginated_request(url, headers, config.get('params', {}))

    if config.get('limit') and config.get('method') in ['POST', 'PUT'] and isinstance(config.get('data'), list):
        return make_chunked_request(config.get('method'), url, headers, config.get('data'), config.get('limit'))

    response = requests.request(
        config.get('method'),
        url,
        headers=headers,
        json=None if config.get('files') else config.get('data'), # Use 'json' parameter if not sending files
        data=config.get('data') if config.get('files') else None, # Use 'data' parameter when sending files
        params=config.get('params'),
        files=config.get('files')
    )

    if response.status_code in [200, 204]:
        return response.json() if response.content else {"status": response.status_code, "title": "No Content"}
    return response.json()

def handle_request(config):
    """
    Handles constructing the request configuration and invoking make_request.

    :param config: Dictionary containing request parameters and options.
    :return: Response from make_request.
    """
    is_multipart = config.get('multipart_parameter') and config.get('multipart_parameter') in config.get('request_data')

    files = None
    if is_multipart:
        files = {config.get('multipart_parameter'): open(config.get('request_data').pop(config.get('multipart_parameter')), 'rb')}

    if config.get('verbose'):
        print("URL:", json.dumps(config.get('url'), indent=4), file=sys.stderr)
        print("Request Data:", json.dumps(config.get('request_data'), indent=4), file=sys.stderr)

    # Build the request-specific config
    request_config = {
        'url': config.get('url'),
        'method': config.get('method'),
        'headers': config.get('headers'),
        'data': config.get('request_data') if config.get('method') in ['POST', 'PUT'] else None,
        'params': config.get('request_data') if config.get('method')in ['GET', 'DELETE'] else None,
        'all_pages': config.get('all_pages', False),
        'store_hash': config.get('store_hash'),
        'auth_token': config.get('auth_token'),
        'files': files,
        'limit': config.get('limit')
    }
    return make_request(request_config)

class UnknownArgumentsCommand(click.Command):
    def format_options(self, ctx, formatter):
        # Collect all options, including custom ones
        opts = []
        for param in self.get_params(ctx):
            rv = param.get_help_record(ctx)
            if rv:
                opts.append(rv)

        # Add custom help text for unknown arguments at the beginning
        opts.insert(0, ('--* TEXT', 'Unknown arguments will be parsed as key-value pairs and merged into the request data JSON object.'))

        # Write options with custom help text
        if opts:
            with formatter.section('Options'):
                formatter.write_dl(opts)

@click.group()
@click.option('--store-hash', envvar='BIGCOMMERCE_STORE_HASH', type=str, help='BigCommerce store hash; Defaults to BIGCOMMERCE_STORE_HASH environment variable.', required=True)
@click.option('--auth-token', envvar='BIGCOMMERCE_AUTH_TOKEN', type=str, help='BigCommerce auth token; Defaults to BIGCOMMERCE_AUTH_TOKEN environment variable.', required=True)
@click.option('--verbose', '-v', is_flag=True, help='Print request data before making the request.')
@click.pass_context
def cli(ctx, store_hash, auth_token, verbose):
    ctx.ensure_object(dict)
    ctx.obj['store_hash'] = store_hash
    ctx.obj['auth_token'] = auth_token
    ctx.obj['verbose'] = verbose

def add_action_commands(command_group, command_dict):
    for action in command_dict.get('actions', []):
        def create_action_command(action):
            @command_group.command(name=action['action'], cls=UnknownArgumentsCommand, context_settings=dict(
                ignore_unknown_options=True,
                allow_extra_args=True,
            ))
            @click.option('--data', type=str, help='Request data as JSON object.')
            @click.pass_context
            @click.argument('unknown_args', nargs=-1, type=click.UNPROCESSED)

            def action_command(ctx, data, unknown_args, **kwargs):
                ctx.obj['data'] = data
                request_data = construct_request_data(ctx.obj, unknown_args)

                # Directly handle the replacement of "-" with stdin content in kwargs
                format_dict = {**ctx.obj, **kwargs}
                for key, value in kwargs.items():
                    if value == "-":
                        format_dict[key] = sys.stdin.read().strip()

                base_url = format_placeholders(command_dict.get('base_url', ''), format_dict)
                endpoint = format_placeholders(command_dict.get('endpoint', ''), format_dict)
                url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
                if url:
                    headers = {
                        **command_dict.get('headers', {}),
                        **command_dict.get('extra_headers', {}),
                    }
                    config = {
                        'url': url,
                        'method': action['method'],
                        'headers': format_placeholders(headers, format_dict),
                        'all_pages': action.get('allPages', False),
                        'multipart_parameter': action.get('multipartParameter', None),
                        'request_data': request_data,
                        'store_hash': ctx.obj['store_hash'],
                        'auth_token': ctx.obj['auth_token'],
                        'verbose': ctx.obj.get('verbose', False),
                        'limit': action.get('limit', None)
                    }
                    response = handle_request(config)
                    print(json.dumps(response, indent=4))
                else:
                    print("An endpoint not defined for this command.", file=sys.stderr)
                    sys.exit(1)

            # Add options for required IDs
            if 'endpoint' in command_dict:
                placeholders = re.findall(r'\{(.*?)\}', command_dict['endpoint'])
                for placeholder in placeholders:
                    options = [f'--{placeholder.replace("_", "-")}']
                    if len(placeholders) == 1:
                        options.append('--id')
                    action_command = click.option(
                        *options,
                        required=True,
                        help=f'The {placeholder} for the endpoint.'
                    )(action_command)

            return action_command
        create_action_command(action)

def build_command_group(parent_group, cmd_config, defaults=None, parent_path=None):
    """
    Recursively build click command groups with actions and subcommands.

    :param parent_group: The parent click.Group to attach this command to
    :param cmd_config: Command config dictionary
    :param defaults: Defaults from parent / global
    :param parent_path: List of parent command names for help messages
    """
    parent_path = parent_path or []

    # Merge defaults: parent < this command
    merged_config = {
        **(defaults or {}),
        **cmd_config,
    }

    # Full command path for help
    full_path = parent_path + [merged_config['command']]
    help_msg = f"Manage {' '.join(full_path)}"

    # Create click group for this command
    group = click.Group(name=merged_config['command'], help=help_msg)
    parent_group.add_command(group)

    # Add actions for this command
    add_action_commands(group, merged_config)

    # Recursively add subcommands
    for subcmd in merged_config.get('subcommands', []):
        # Merge defaults with parent command values, letting subcmd override
        subcmd_defaults = {
            **defaults,
            "base_url": cmd_config.get("base_url", defaults.get("base_url")),
            "extra_headers": {
                **defaults.get("extra_headers", {}),
                **cmd_config.get("extra_headers", {})
            }
        }
        build_command_group(group, subcmd, defaults=subcmd_defaults, parent_path=full_path)

    return group

def build_commands(commands_dict):
    global_defaults = commands_dict.get('default', {})

    # Build all top-level commands and subcommands
    for cmd_config in commands_dict['commands']:
        build_command_group(cli, cmd_config, defaults=global_defaults)

def main():
    commands_structure = {
        'default': {
            'base_url': 'https://api.bigcommerce.com/stores/{store_hash}/',
            'headers': {
                'X-Auth-Token': '{auth_token}',
                'Accept': 'application/json',
                'Content-Type': 'application/json',
            },
        },
        'commands': [
            {
                'command': 'product',
                'endpoint': 'v3/catalog/products/{product_id}',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'create', 'method': 'POST'},
                    {'action': 'update', 'method': 'PUT'},
                    {'action': 'delete', 'method': 'DELETE'},
                ],
                'subcommands': [
                    {
                        'command': 'variant',
                        'endpoint': 'v3/catalog/products/{product_id}/variants/{variant_id}',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'update', 'method': 'PUT'},
                            {'action': 'delete', 'method': 'DELETE'},
                        ],
                        'subcommands': [
                            {
                                'command': 'metafield',
                                'endpoint': 'v3/catalog/products/{product_id}/variants/{variant_id}/metafields/{metafield_id}',
                                'actions': [
                                    {'action': 'get', 'method': 'GET'},
                                    {'action': 'update', 'method': 'PUT'},
                                    {'action': 'delete', 'method': 'DELETE'},
                                ]
                            },
                            {
                                'command': 'metafields',
                                'endpoint': 'v3/catalog/products/{product_id}/variants/{variant_id}/metafields',
                                'actions': [
                                    {'action': 'get', 'method': 'GET'},
                                    {'action': 'get-all', 'method': 'GET', 'allPages': True},
                                    {'action': 'create', 'method': 'POST'},
                                ]
                            }
                        ]
                    },
                    {
                        'command': 'variants',
                        'endpoint': 'v3/catalog/products/{product_id}/variants',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'get-all', 'method': 'GET', 'allPages': True},
                            {'action': 'create', 'method': 'POST'},
                        ]
                    },
                    {
                        'command': 'metafield',
                        'endpoint': 'v3/catalog/products/{product_id}/metafields/{metafield_id}',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'update', 'method': 'PUT'},
                            {'action': 'delete', 'method': 'DELETE'},
                        ]
                    },
                    {
                        'command': 'metafields',
                        'endpoint': 'v3/catalog/products/{product_id}/metafields',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'get-all', 'method': 'GET', 'allPages': True},
                            {'action': 'create', 'method': 'POST'},
                        ]
                    },
                    {
                        'command': 'custom-field',
                        'endpoint': 'v3/catalog/products/{product_id}/custom-fields/{custom_field_id}',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'update', 'method': 'PUT'},
                            {'action': 'delete', 'method': 'DELETE'},
                        ]
                    },
                    {
                        'command': 'custom-fields',
                        'endpoint': 'v3/catalog/products/{product_id}/custom-fields',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'get-all', 'method': 'GET', 'allPages': True},
                            {'action': 'create', 'method': 'POST'},
                        ]
                    },
                    {
                        'command': 'image',
                        'endpoint': 'v3/catalog/products/{product_id}/images/{image_id}',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'update', 'method': 'PUT'},
                            {'action': 'delete', 'method': 'DELETE'},
                        ]
                    },
                    {
                        'command': 'images',
                        'endpoint': 'v3/catalog/products/{product_id}/images',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'get-all', 'method': 'GET', 'allPages': True},
                            {'action': 'create', 'method': 'POST', 'multipartParamter': 'image_file'},
                        ]
                    },
                    {
                        'command': 'option',
                        'endpoint': 'v3/catalog/products/{product_id}/options/{option_id}',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'update', 'method': 'PUT'},
                            {'action': 'delete', 'method': 'DELETE'},
                        ]
                    },
                    {
                        'command': 'options',
                        'endpoint': 'v3/catalog/products/{product_id}/options',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'get-all', 'method': 'GET', 'allPages': True},
                            {'action': 'create', 'method': 'POST'},
                        ]
                    }
                ]
            },
            {
                'command': 'products',
                'endpoint': 'v3/catalog/products',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'get-all', 'method': 'GET', 'allPages': True},
                    {'action': 'create', 'method': 'POST'},
                    {'action': 'update', 'method': 'PUT'},
                    {'action': 'delete', 'method': 'DELETE'},
                ]
            },
            {
                'command': 'variants',
                'endpoint': 'v3/catalog/variants',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'get-all', 'method': 'GET', 'allPages': True},
                    {'action': 'upsert', 'method': 'PUT', 'limit': 50},
                ]
            },
            {
                'command': 'category-tree',
                'endpoint': 'v3/catalog/trees/{tree_id}/categories',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'update', 'method': 'PUT'},
                    {'action': 'delete', 'method': 'DELETE'},
                ]
            },
            {
                'command': 'category-trees',
                'endpoint': 'v3/catalog/trees',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'get-all', 'method': 'GET', 'allPages': True},
                    {'action': 'create', 'method': 'POST'},
                    {'action': 'update', 'method': 'PUT'},
                    {'action': 'delete', 'method': 'DELETE'},
                ]
            },
            {
                'command': 'category',
                'subcommands': [
                    {
                        'command': 'metafield',
                        'endpoint': 'v3/catalog/categories/{category_id}/metafields/{metafield_id}',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'update', 'method': 'PUT'},
                            {'action': 'delete', 'method': 'DELETE'},
                        ]
                    },
                    {
                        'command': 'metafields',
                        'endpoint': 'v3/catalog/categories/{category_id}/metafields',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'get-all', 'method': 'GET', 'allPages': True},
                            {'action': 'create', 'method': 'POST'},
                        ]
                    },
                    {
                        'command': 'image',
                        'endpoint': 'v3/catalog/categories/{category_id}/image',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'create', 'method': 'POST', 'multipartParamter': 'image_file'},
                            {'action': 'update', 'method': 'PUT'},
                            {'action': 'delete', 'method': 'DELETE'},
                        ]
                    }
                ]
            },
            {
                'command': 'categories',
                'endpoint': 'v3/catalog/trees/categories',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'get-all', 'method': 'GET', 'allPages': True},
                    {'action': 'create', 'method': 'POST'},
                    {'action': 'update', 'method': 'PUT'},
                    {'action': 'delete', 'method': 'DELETE'},
                ]
            },
            {
                'command': 'customer',
                'subcommands': [
                    {
                        'command': 'metafields',
                        'endpoint': 'v3/customers/{customer_id}/metafields',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'get-all', 'method': 'GET', 'allPages': True},
                            {'action': 'create', 'method': 'POST'},
                            {'action': 'update', 'method': 'PUT'},
                            {'action': 'delete', 'method': 'DELETE'},
                        ]
                    }
                ]
            },
            {
                'command': 'customers',
                'endpoint': 'v3/customers',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'get-all', 'method': 'GET', 'allPages': True},
                    {'action': 'create', 'method': 'POST'},
                    {'action': 'update', 'method': 'PUT'},
                    {'action': 'delete', 'method': 'DELETE'},
                ],
                'subcommands': [
                    {
                        'command': 'attributes',
                        'endpoint': 'v3/customers/attributes',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'get-all', 'method': 'GET', 'allPages': True},
                            {'action': 'create', 'method': 'POST'},
                            {'action': 'update', 'method': 'PUT'},
                            {'action': 'delete', 'method': 'DELETE'},
                        ]
                    }
                ]
            },
            {
                'command': 'order',
                'endpoint': 'v2/orders/{order_id}',
                'subcommands': [
                    {
                        'command': 'metafields',
                        'endpoint': 'v3/orders/{order_id}/metafields',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'create', 'method': 'POST'},
                            {'action': 'update', 'method': 'PUT'},
                            {'action': 'delete', 'method': 'DELETE'},
                        ]
                    }
                ]
            },
            {
                'command': 'orders',
                'endpoint': 'v2/orders',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'get-all', 'method': 'GET', 'allPages': True},
                    {'action': 'create', 'method': 'POST'},
                    {'action': 'delete', 'method': 'DELETE'},
                ]
            },
            {
                'command': 'page',
                'endpoint': 'v3/content/pages/{page_id}',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'update', 'method': 'PUT'},
                    {'action': 'delete', 'method': 'DELETE'},
                ]
            },
            {
                'command': 'pages',
                'endpoint': 'v3/content/pages',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'get-all', 'method': 'GET', 'allPages': True},
                    {'action': 'create', 'method': 'POST'},
                    {'action': 'update', 'method': 'PUT'},
                    {'action': 'delete', 'method': 'DELETE'},
                ]
            },
            {
                'command': 'redirects',
                'endpoint': 'v3/storefront/redirects',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'get-all', 'method': 'GET', 'allPages': True},
                    {'action': 'upsert', 'method': 'PUT'},
                    {'action': 'delete', 'method': 'DELETE'},
                ]
            },
            {
                'command': 'site',
                'endpoint': 'v3/sites/{site_id}',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'update', 'method': 'PUT'},
                    {'action': 'delete', 'method': 'DELETE'},
                ]
            },
            {
                'command': 'sites',
                'endpoint': 'v3/sites',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'get-all', 'method': 'GET', 'allPages': True},
                    {'action': 'create', 'method': 'POST'},
                ]
            },
            {
                'command': 'widget-template',
                'endpoint': 'v3/content/widget-templates/{uuid}',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'update', 'method': 'PUT'},
                    {'action': 'delete', 'method': 'DELETE'},
                ],
                'subcommands': [
                    {
                        'command': 'render',
                        'endpoint': 'v3/content/widget-templates/{uuid}/preview',
                        'actions': [
                            {'action': 'create', 'method': 'POST'},
                        ]
                    }
                ]
            },
            {
                'command': 'widget-templates',
                'endpoint': 'v3/content/widget-templates',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'get-all', 'method': 'GET', 'allPages': True},
                    {'action': 'create', 'method': 'POST'},
                ]
            },
            {
                'command': 'widget',
                'endpoint': 'v3/content/widgets/{uuid}',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'update', 'method': 'PUT'},
                    {'action': 'delete', 'method': 'DELETE'},
                ]
            },
            {
                'command': 'widgets',
                'endpoint': 'v3/content/widgets',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'get-all', 'method': 'GET', 'allPages': True},
                    {'action': 'create', 'method': 'POST'},
                ]
            },
            {
                'command': 'placement',
                'endpoint': 'v3/content/placements/{uuid}',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'update', 'method': 'PUT'},
                    {'action': 'delete', 'method': 'DELETE'},
                ]
            },
            {
                'command': 'placements',
                'endpoint': 'v3/content/placements',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'get-all', 'method': 'GET', 'allPages': True},
                    {'action': 'create', 'method': 'POST'},
                ]
            },
            {
                'command': 'regions',
                'endpoint': 'v3/content/regions',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                ]
            },
            {
                'command': 'custom-template-associations',
                'endpoint': 'v3/storefront/custom-template-associations',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'get-all', 'method': 'GET', 'allPages': True},
                    {'action': 'create', 'method': 'PUT'},
                    {'action': 'update', 'method': 'PUT'},
                    {'action': 'delete', 'method': 'DELETE'},
                ]
            },
            {
                'command': 'themes',
                'endpoint': 'v3/themes',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'get-all', 'method': 'GET', 'allPages': True},
                    {'action': 'upload', 'method': 'POST'},
                ],
                'subcommands': [
                    {
                        'command': 'custom-templates',
                        'endpoint': 'v3/themes/custom-templates/{version_uuid}',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'get-all', 'method': 'GET', 'allPages': True},
                        ]
                    },
                    {
                        'command': 'activate',
                        'endpoint': 'v3/themes/actions/activate',
                        'actions': [
                            {'action': 'set', 'method': 'POST'},
                        ]
                    }
                ]
            },
            {
                'command': 'theme',
                'endpoint': 'v3/themes/{uuid}',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'delete', 'method': 'DELETE'},
                ],
                'subcommands': [
                    {
                        'command': 'configurations',
                        'endpoint': 'v3/themes/{uuid}/configurations',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'get-all', 'method': 'GET', 'allPages': True},
                        ]
                    },
                    {
                        'command': 'configuration',
                        'endpoint': 'v3/themes/{uuid}/configurations/validate',
                        'actions': [
                            {'action': 'validate', 'method': 'POST'},
                        ]
                    }
                ]
            },
            {
                'command': 'channels',
                'endpoint': 'v3/channels',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'get-all', 'method': 'GET', 'allPages': True},
                    {'action': 'create', 'method': 'POST'},
                ]
            },
            {
                'command': 'channel',
                'endpoint': 'v3/channels/{channel_id}',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'update', 'method': 'PUT'},
                ],
                'subcommands': [
                    {
                        'command': 'active-theme',
                        'endpoint': 'v3/channels/{channel_id}/active-theme',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                        ]
                    }
                ]
            },
            {
                'command': 'blog-posts',
                'endpoint': 'v2/blog/posts',
                'actions': [
                    {'action': 'get-all', 'method': 'GET'},
                    {'action': 'create', 'method': 'POST'},
                ],
                'subcommands': [
                    {
                        'command': 'count',
                        'endpoint': 'v2/blog/posts/count',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                        ]
                    }
                ]
            },
            {
                'command': 'blog-post',
                'endpoint': 'v2/blog/posts/{id}',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'update', 'method': 'PUT'},
                    {'action': 'delete', 'method': 'DELETE'},
                ]
            },
            {
                'command': 'blog-tags',
                'endpoint': 'v2/blog/tags',
                'actions': [
                    {'action': 'get-all', 'method': 'GET'},
                ]
            },
            {
                'command': 'shipping-zone',
                'endpoint': 'v2/shipping/zones/{id}',
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'update', 'method': 'PUT'},
                    {'action': 'delete', 'method': 'DELETE'},
                ],
                'subcommands': [
                    {
                        'command': 'method',
                        'endpoint': 'v2/shipping/zones/{zone_id}/methods/{method_id}',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'update', 'method': 'PUT'},
                            {'action': 'delete', 'method': 'DELETE'},
                        ]
                    },
                    {
                        'command': 'methods',
                        'endpoint': 'v2/shipping/zones/{zone_id}/methods',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'get-all', 'method': 'GET', 'allPages': True},
                            {'action': 'create', 'method': 'POST'},
                        ]
                    }
                ]
            },
            {
                'command': 'shipping-zones',
                'endpoint': 'v2/shipping/zones',
                'actions': [
                    {'action': 'get-all', 'method': 'GET'},
                    {'action': 'create', 'method': 'POST'},
                ]
            },
            {
                'command': 'company',
                'base_url': 'https://api-b2b.bigcommerce.com/api/',
                'endpoint': 'v3/io/companies/{company_id}',
                'extra_headers': {
                    'X-Store-Hash': '{store_hash}',
                },
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'update', 'method': 'PUT'},
                    {'action': 'delete', 'method': 'DELETE'},
                ],
                'subcommands': [
                    {
                        'command': 'subsidiary',
                        'endpoint': 'v3/io/companies/{company_id}/subsidiaries/{child_company_id}',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'delete', 'method': 'DELETE'},
                        ],
                    },
                    {
                        'command': 'subsidiaries',
                        'endpoint': 'v3/io/companies/{company_id}/subsidiaries',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'get-all', 'method': 'GET', 'allPages': True},
                        ],
                    },
                    {
                        'command': 'hierarchy',
                        'endpoint': 'v3/io/companies/{company_id}/hierarchy',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'get-all', 'method': 'GET', 'allPages': True},
                        ],
                    },
                ]
            },
            {
                'command': 'companies',
                'base_url': 'https://api-b2b.bigcommerce.com/api/',
                'endpoint': 'v3/io/companies',
                'extra_headers': {
                    'X-Store-Hash': '{store_hash}',
                },
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'get-all', 'method': 'GET', 'allPages': True},
                    {'action': 'create', 'method': 'POST'},
                ],
                'subcommands': [
                    {
                        'command': 'bulk',
                        'endpoint': 'v3/io/companies/bulk',
                        'actions': [
                            {'action': 'create', 'method': 'POST'},
                            {'action': 'update', 'method': 'PUT'},
                        ],
                    },
                ]
            },
            {
                'command': 'shopping-list',
                'base_url': 'https://api-b2b.bigcommerce.com/api/',
                'endpoint': 'v3/io/shopping-list/{shopping_list_id}',
                'extra_headers': {
                    'X-Store-Hash': '{store_hash}',
                },
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'update', 'method': 'PUT'},
                    {'action': 'delete', 'method': 'DELETE'},
                ],
                'subcommands': [
                    {
                        'command': 'items',
                        'endpoint': 'v3/io/shopping-list/{shopping_list_id}/items/{item_id}',
                        'actions': [
                            {'action': 'delete', 'method': 'DELETE'},
                        ],
                    },
                ]
            },
            {
                'command': 'shopping-lists',
                'base_url': 'https://api-b2b.bigcommerce.com/api/',
                'endpoint': 'v3/io/shopping-list',
                'extra_headers': {
                    'X-Store-Hash': '{store_hash}',
                },
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'get-all', 'method': 'GET', 'allPages': True},
                    {'action': 'create', 'method': 'POST'},
                ]
            },
            {
                'command': 'user',
                'base_url': 'https://api-b2b.bigcommerce.com/api/',
                'endpoint': 'v3/io/users/{user_id}',
                'extra_headers': {
                    'X-Store-Hash': '{store_hash}',
                },
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'update', 'method': 'PUT'},
                    {'action': 'delete', 'method': 'DELETE'},
                ],
                'subcommands': [
                    {
                        'command': 'by-customer-id',
                        'endpoint': 'v3/io/users/customer/{customer_id}',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                        ],
                    },
                ]
            },
            {
                'command': 'users',
                'base_url': 'https://api-b2b.bigcommerce.com/api/',
                'endpoint': 'v3/io/users',
                'extra_headers': {
                    'X-Store-Hash': '{store_hash}',
                },
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'get-all', 'method': 'GET', 'allPages': True},
                    {'action': 'create', 'method': 'POST'},
                ],
                'subcommands': [
                    {
                        'command': 'bulk',
                        'endpoint': 'v3/io/companies/bulk',
                        'actions': [
                            {'action': 'create', 'method': 'POST'},
                        ],
                    },
                ]
            },
            {
                'command': 'address',
                'base_url': 'https://api-b2b.bigcommerce.com/api/',
                'endpoint': 'v3/io/addresses/{address_id}',
                'extra_headers': {
                    'X-Store-Hash': '{store_hash}',
                },
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'update', 'method': 'PUT'},
                    {'action': 'delete', 'method': 'DELETE'},
                ]
            },
            {
                'command': 'addresses',
                'base_url': 'https://api-b2b.bigcommerce.com/api/',
                'endpoint': 'v3/io/addresses',
                'extra_headers': {
                    'X-Store-Hash': '{store_hash}',
                },
                'actions': [
                    {'action': 'get', 'method': 'GET'},
                    {'action': 'get-all', 'method': 'GET', 'allPages': True},
                    {'action': 'create', 'method': 'POST'},
                ],
                'subcommands': [
                    {
                        'command': 'bulk',
                        'endpoint': 'v3/io/companies/bulk',
                        'actions': [
                            {'action': 'create', 'method': 'POST'},
                        ],
                    },
                    {
                        'command': 'extra-fields',
                        'endpoint': 'v3/io/companies/extra-fields',
                        'actions': [
                            {'action': 'get', 'method': 'GET'},
                            {'action': 'get-all', 'method': 'GET', 'allPages': True},
                        ],
                    },
                ]
            },
        ]
    }
    build_commands(commands_structure)
    cli(obj={})

if __name__ == '__main__':
    main()
