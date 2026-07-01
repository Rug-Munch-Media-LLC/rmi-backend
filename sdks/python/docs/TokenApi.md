# rugmunch.TokenApi

All URIs are relative to *http://localhost*

Method | HTTP request | Description
------------- | ------------- | -------------
[**get_detail_api_v1_token_address_get**](TokenApi.md#get_detail_api_v1_token_address_get) | **GET** /api/v1/token/{address} | Get Detail
[**get_holders_api_v1_token_address_holders_get**](TokenApi.md#get_holders_api_v1_token_address_holders_get) | **GET** /api/v1/token/{address}/holders | Get Holders
[**get_liquidity_api_v1_token_address_liquidity_get**](TokenApi.md#get_liquidity_api_v1_token_address_liquidity_get) | **GET** /api/v1/token/{address}/liquidity | Get Liquidity
[**get_risk_api_v1_token_address_risk_get**](TokenApi.md#get_risk_api_v1_token_address_risk_get) | **GET** /api/v1/token/{address}/risk | Get Risk
[**scan_api_v1_token_scan_post**](TokenApi.md#scan_api_v1_token_scan_post) | **POST** /api/v1/token/scan | Scan


# **get_detail_api_v1_token_address_get**
> TokenDetail get_detail_api_v1_token_address_get(address, chain=chain)

Get Detail

Token metadata + supply + verification.

### Example


```python
import rugmunch
from rugmunch.models.token_detail import TokenDetail
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
    api_instance = rugmunch.TokenApi(api_client)
    address = 'address_example' # str | 
    chain = 'solana' # str |  (optional) (default to 'solana')

    try:
        # Get Detail
        api_response = api_instance.get_detail_api_v1_token_address_get(address, chain=chain)
        print("The response of TokenApi->get_detail_api_v1_token_address_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling TokenApi->get_detail_api_v1_token_address_get: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **address** | **str**|  | 
 **chain** | **str**|  | [optional] [default to &#39;solana&#39;]

### Return type

[**TokenDetail**](TokenDetail.md)

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

# **get_holders_api_v1_token_address_holders_get**
> PaginatedResponse get_holders_api_v1_token_address_holders_get(address, chain=chain, limit=limit)

Get Holders

Top token holders.

### Example


```python
import rugmunch
from rugmunch.models.paginated_response import PaginatedResponse
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
    api_instance = rugmunch.TokenApi(api_client)
    address = 'address_example' # str | 
    chain = 'solana' # str |  (optional) (default to 'solana')
    limit = 100 # int |  (optional) (default to 100)

    try:
        # Get Holders
        api_response = api_instance.get_holders_api_v1_token_address_holders_get(address, chain=chain, limit=limit)
        print("The response of TokenApi->get_holders_api_v1_token_address_holders_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling TokenApi->get_holders_api_v1_token_address_holders_get: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **address** | **str**|  | 
 **chain** | **str**|  | [optional] [default to &#39;solana&#39;]
 **limit** | **int**|  | [optional] [default to 100]

### Return type

[**PaginatedResponse**](PaginatedResponse.md)

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

# **get_liquidity_api_v1_token_address_liquidity_get**
> Dict[str, object] get_liquidity_api_v1_token_address_liquidity_get(address, chain=chain)

Get Liquidity

Token liquidity + pool info.

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
    api_instance = rugmunch.TokenApi(api_client)
    address = 'address_example' # str | 
    chain = 'solana' # str |  (optional) (default to 'solana')

    try:
        # Get Liquidity
        api_response = api_instance.get_liquidity_api_v1_token_address_liquidity_get(address, chain=chain)
        print("The response of TokenApi->get_liquidity_api_v1_token_address_liquidity_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling TokenApi->get_liquidity_api_v1_token_address_liquidity_get: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **address** | **str**|  | 
 **chain** | **str**|  | [optional] [default to &#39;solana&#39;]

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
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_risk_api_v1_token_address_risk_get**
> TokenRisk get_risk_api_v1_token_address_risk_get(address, chain=chain)

Get Risk

Combined risk: holders + liquidity + heuristics.

### Example


```python
import rugmunch
from rugmunch.models.token_risk import TokenRisk
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
    api_instance = rugmunch.TokenApi(api_client)
    address = 'address_example' # str | 
    chain = 'solana' # str |  (optional) (default to 'solana')

    try:
        # Get Risk
        api_response = api_instance.get_risk_api_v1_token_address_risk_get(address, chain=chain)
        print("The response of TokenApi->get_risk_api_v1_token_address_risk_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling TokenApi->get_risk_api_v1_token_address_risk_get: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **address** | **str**|  | 
 **chain** | **str**|  | [optional] [default to &#39;solana&#39;]

### Return type

[**TokenRisk**](TokenRisk.md)

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

# **scan_api_v1_token_scan_post**
> TokenScanResult scan_api_v1_token_scan_post(token_scan_request)

Scan

Full token scan. Multi-module. Freemium rate-limit applied in legacy router.

### Example


```python
import rugmunch
from rugmunch.models.token_scan_request import TokenScanRequest
from rugmunch.models.token_scan_result import TokenScanResult
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
    api_instance = rugmunch.TokenApi(api_client)
    token_scan_request = rugmunch.TokenScanRequest() # TokenScanRequest | 

    try:
        # Scan
        api_response = api_instance.scan_api_v1_token_scan_post(token_scan_request)
        print("The response of TokenApi->scan_api_v1_token_scan_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling TokenApi->scan_api_v1_token_scan_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **token_scan_request** | [**TokenScanRequest**](TokenScanRequest.md)|  | 

### Return type

[**TokenScanResult**](TokenScanResult.md)

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: application/json
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

