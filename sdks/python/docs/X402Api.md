# rugmunch.X402Api

All URIs are relative to *http://localhost*

Method | HTTP request | Description
------------- | ------------- | -------------
[**catalog_api_v1_x402_catalog_get**](X402Api.md#catalog_api_v1_x402_catalog_get) | **GET** /api/v1/x402/catalog | Catalog
[**receipt_api_v1_x402_receipts_tx_hash_get**](X402Api.md#receipt_api_v1_x402_receipts_tx_hash_get) | **GET** /api/v1/x402/receipts/{tx_hash} | Receipt
[**usage_api_v1_x402_usage_get**](X402Api.md#usage_api_v1_x402_usage_get) | **GET** /api/v1/x402/usage | Usage
[**verify_payment_api_v1_x402_verify_post**](X402Api.md#verify_payment_api_v1_x402_verify_post) | **POST** /api/v1/x402/verify | Verify Payment


# **catalog_api_v1_x402_catalog_get**
> CatalogResponse catalog_api_v1_x402_catalog_get()

Catalog

List all paid tools with pricing tiers.

### Example


```python
import rugmunch
from rugmunch.models.catalog_response import CatalogResponse
from rugmunch.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = rugmunch.Configuration(
    host = "http://localhost"
)


# Enter a context with an instance of the API client
with rugmunch.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = rugmunch.X402Api(api_client)

    try:
        # Catalog
        api_response = api_instance.catalog_api_v1_x402_catalog_get()
        print("The response of X402Api->catalog_api_v1_x402_catalog_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling X402Api->catalog_api_v1_x402_catalog_get: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

[**CatalogResponse**](CatalogResponse.md)

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **receipt_api_v1_x402_receipts_tx_hash_get**
> ReceiptResponse receipt_api_v1_x402_receipts_tx_hash_get(tx_hash)

Receipt

Payment receipt for a specific on-chain tx.

### Example


```python
import rugmunch
from rugmunch.models.receipt_response import ReceiptResponse
from rugmunch.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = rugmunch.Configuration(
    host = "http://localhost"
)


# Enter a context with an instance of the API client
with rugmunch.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = rugmunch.X402Api(api_client)
    tx_hash = 'tx_hash_example' # str | 

    try:
        # Receipt
        api_response = api_instance.receipt_api_v1_x402_receipts_tx_hash_get(tx_hash)
        print("The response of X402Api->receipt_api_v1_x402_receipts_tx_hash_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling X402Api->receipt_api_v1_x402_receipts_tx_hash_get: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **tx_hash** | **str**|  | 

### Return type

[**ReceiptResponse**](ReceiptResponse.md)

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **usage_api_v1_x402_usage_get**
> UsageResponse usage_api_v1_x402_usage_get()

Usage

Per-agent usage stats: calls made, $ spent, top tools.

### Example


```python
import rugmunch
from rugmunch.models.usage_response import UsageResponse
from rugmunch.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = rugmunch.Configuration(
    host = "http://localhost"
)


# Enter a context with an instance of the API client
with rugmunch.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = rugmunch.X402Api(api_client)

    try:
        # Usage
        api_response = api_instance.usage_api_v1_x402_usage_get()
        print("The response of X402Api->usage_api_v1_x402_usage_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling X402Api->usage_api_v1_x402_usage_get: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

[**UsageResponse**](UsageResponse.md)

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **verify_payment_api_v1_x402_verify_post**
> Dict[str, object] verify_payment_api_v1_x402_verify_post()

Verify Payment

Verify an X-Payment header for a given tool.

### Example


```python
import rugmunch
from rugmunch.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = rugmunch.Configuration(
    host = "http://localhost"
)


# Enter a context with an instance of the API client
with rugmunch.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = rugmunch.X402Api(api_client)

    try:
        # Verify Payment
        api_response = api_instance.verify_payment_api_v1_x402_verify_post()
        print("The response of X402Api->verify_payment_api_v1_x402_verify_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling X402Api->verify_payment_api_v1_x402_verify_post: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

**Dict[str, object]**

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

