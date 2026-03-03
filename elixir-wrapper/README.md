# Elixir SDK Test Wrapper

Test wrapper service for the ABSmartly Elixir SDK.

## Building

```bash
docker-compose build elixir-sdk
```

## Running

```bash
docker-compose up elixir-sdk
```

## Testing

```bash
# Run tests for Elixir SDK only
./run-tests.sh --sdk elixir-sdk

# Or include in full test suite
./run-tests.sh
```

## Port

The wrapper runs on port **3016** (mapped in docker-compose.yml).
