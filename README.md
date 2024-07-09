# BigCommerce Toolkit

BigCommerce Toolkit is a command-line interface (CLI) tool for BigCommerce's API. It follows UNIX principles of "do one thing well" and organizes commands based on resource type and action. This structure aligns with RESTful API principles, providing a logical and hierarchical system for managing various BigCommerce resources.

## Features

- **Resource-based Commands:** Commands are structured around resource types (e.g., products, categories, customers) and their respective actions (e.g., get, add, update, delete).
- **Hierarchical Command Structure:** Similar to UNIX tools like `git`, commands are grouped logically to align with BigCommerce's API structure.
- **Environment Variable Support:** Store hash and authentication token can be set via environment variables for convenience.
- **Paginated Requests:** Supports fetching all pages of data for GET requests with pagination.
- **Standard Input (stdin) Support:** Allows reading values from stdin for easier scripting and piping data between commands.

## Installation

To install BigCommerce Toolkit, clone the repository and install the necessary dependencies.

```sh
pip install bigcommerce-toolkit
```

## Usage

### Basic Command Structure

The CLI uses a structure similar to UNIX commands, where you specify the resource type, action, and additional parameters or options as needed.

```sh
bigc [<options>] <resource> [<subresource>] <action> [<arguments>]
```

### Setting Up Environment Variables

Before using the tool, set the environment variables for your BigCommerce store hash and authentication token.

```sh
export BIGCOMMERCE_STORE_HASH=your_store_hash
export BIGCOMMERCE_AUTH_TOKEN=your_auth_token
```

Alternatively, you can pass these values directly via command-line options.

```sh
bigc --store-hash your_store_hash --auth-token your_auth_token …
```

### Example Commands

## Add a Product (via Arguments)

To add a new product with data provided as named arguments:

```sh
bigc products add --name "New Product" --price 19.99 --type physical --weight 0
```

## Add a Product (via Standard Input)

BigCommerce Toolkit also supports reading values from `stdin`, allowing for piping data between commands for easier scripting. For example:

```sh
echo '{"name": "New Product", "price": 19.99, "type": "physical", "weight": 0}' | bigc products add --data -
```

### Update a Product

This can be further leveraged by piping through additional tools like `jq`. First, retrieving a product's ID by the product's name, and then updating that product's price:

```sh
bigc products get --name:like "New Product" | jq -r '.data[0].id' | bigc product update --id - --price 24.99
```

## Fetching All Products

For endpoints that support pagination, you can fetch all pages of data. Using tools like `jq` and `csvlook`, it is possible to format the data into a more readable format.

```sh
bigc products get-all | jq -r '["id","sku","name"], (.data[] | [.id,.sku,.name]) | @csv' | csvlook
```

## Contributing

We welcome contributions to improve the project. Please submit issues and pull requests via GitHub.
