# rugmunch.WalletApi

All URIs are relative to *http://localhost*

Method | HTTP request | Description
------------- | ------------- | -------------
[**get_analysis_api_v1_wallet_address_analysis_get**](WalletApi.md#get_analysis_api_v1_wallet_address_analysis_get) | **GET** /api/v1/wallet/{address}/analysis | Get Analysis
[**get_balance_api_v1_wallet_address_balance_get**](WalletApi.md#get_balance_api_v1_wallet_address_balance_get) | **GET** /api/v1/wallet/{address}/balance | Get Balance
[**get_transactions_api_v1_wallet_address_transactions_get**](WalletApi.md#get_transactions_api_v1_wallet_address_transactions_get) | **GET** /api/v1/wallet/{address}/transactions | Get Transactions
[**scan_api_v1_wallet_scan_post**](WalletApi.md#scan_api_v1_wallet_scan_post) | **POST** /api/v1/wallet/scan | Scan


# **get_analysis_api_v1_wallet_address_analysis_get**
> WalletAnalysis get_analysis_api_v1_wallet_address_analysis_get(address, chain=chain)

Get Analysis

Full wallet analysis: risk score + tokens + recent transactions.

### Example


```python
import rugmunch
from rugmunch.models.wallet_analysis import WalletAnalysis
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
    api_instance = rugmunch.WalletApi(api_client)
    address = 'address_example' # str | 
    chain = 'solana' # str |  (optional) (default to 'solana')

    try:
        # Get Analysis
        api_response = api_instance.get_analysis_api_v1_wallet_address_analysis_get(address, chain=chain)
        print("The response of WalletApi->get_analysis_api_v1_wallet_address_analysis_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling WalletApi->get_analysis_api_v1_wallet_address_analysis_get: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **address** | **str**|  | 
 **chain** | **str**|  | [optional] [default to &#39;solana&#39;]

### Return type

[**WalletAnalysis**](WalletAnalysis.md)

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

# **get_balance_api_v1_wallet_address_balance_get**
> Balance get_balance_api_v1_wallet_address_balance_get(address, chain=chain)

Get Balance

Wallet balance + token holdings.

### Example


```python
import rugmunch
from rugmunch.models.balance import Balance
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
    api_instance = rugmunch.WalletApi(api_client)
    address = 'address_example' # str | 
    chain = 'solana' # str |  (optional) (default to 'solana')

    try:
        # Get Balance
        api_response = api_instance.get_balance_api_v1_wallet_address_balance_get(address, chain=chain)
        print("The response of WalletApi->get_balance_api_v1_wallet_address_balance_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling WalletApi->get_balance_api_v1_wallet_address_balance_get: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **address** | **str**|  | 
 **chain** | **str**|  | [optional] [default to &#39;solana&#39;]

### Return type

[**Balance**](Balance.md)

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

# **get_transactions_api_v1_wallet_address_transactions_get**
> PaginatedResponse get_transactions_api_v1_wallet_address_transactions_get(address, chain=chain, limit=limit)

Get Transactions

Recent transactions for a wallet.

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
    api_instance = rugmunch.WalletApi(api_client)
    address = 'address_example' # str | 
    chain = 'solana' # str |  (optional) (default to 'solana')
    limit = 50 # int |  (optional) (default to 50)

    try:
        # Get Transactions
        api_response = api_instance.get_transactions_api_v1_wallet_address_transactions_get(address, chain=chain, limit=limit)
        print("The response of WalletApi->get_transactions_api_v1_wallet_address_transactions_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling WalletApi->get_transactions_api_v1_wallet_address_transactions_get: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **address** | **str**|  | 
 **chain** | **str**|  | [optional] [default to &#39;solana&#39;]
 **limit** | **int**|  | [optional] [default to 50]

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

# **scan_api_v1_wallet_scan_post**
> ScanResult scan_api_v1_wallet_scan_post(app_domain_wallet_models_scan_request)

Scan

Multi-chain threat scan. Auth optional — freemium rate-limit applied in legacy router.

### Example


```python
import rugmunch
from rugmunch.models.app_domain_wallet_models_scan_request import AppDomainWalletModelsScanRequest
from rugmunch.models.scan_result import ScanResult
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
    api_instance = rugmunch.WalletApi(api_client)
    app_domain_wallet_models_scan_request = rugmunch.AppDomainWalletModelsScanRequest() # AppDomainWalletModelsScanRequest | 

    try:
        # Scan
        api_response = api_instance.scan_api_v1_wallet_scan_post(app_domain_wallet_models_scan_request)
        print("The response of WalletApi->scan_api_v1_wallet_scan_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling WalletApi->scan_api_v1_wallet_scan_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **app_domain_wallet_models_scan_request** | [**AppDomainWalletModelsScanRequest**](AppDomainWalletModelsScanRequest.md)|  | 

### Return type

[**ScanResult**](ScanResult.md)

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

