# rugmunch.ScannerApi

All URIs are relative to *http://localhost*

Method | HTTP request | Description
------------- | ------------- | -------------
[**scan_api_v1_scanner_scan_post**](ScannerApi.md#scan_api_v1_scanner_scan_post) | **POST** /api/v1/scanner/scan | Scan


# **scan_api_v1_scanner_scan_post**
> ScanResponse scan_api_v1_scanner_scan_post(app_domain_scanner_models_scan_request)

Scan

Full token scan. Multi-module. Returns Pydantic response.

The legacy /api/v1/scanner/scan (and /api/v1/token/scan) are still
served — they take priority on the same path during strangelfig.

### Example


```python
import rugmunch
from rugmunch.models.app_domain_scanner_models_scan_request import AppDomainScannerModelsScanRequest
from rugmunch.models.scan_response import ScanResponse
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
    api_instance = rugmunch.ScannerApi(api_client)
    app_domain_scanner_models_scan_request = rugmunch.AppDomainScannerModelsScanRequest() # AppDomainScannerModelsScanRequest | 

    try:
        # Scan
        api_response = api_instance.scan_api_v1_scanner_scan_post(app_domain_scanner_models_scan_request)
        print("The response of ScannerApi->scan_api_v1_scanner_scan_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling ScannerApi->scan_api_v1_scanner_scan_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **app_domain_scanner_models_scan_request** | [**AppDomainScannerModelsScanRequest**](AppDomainScannerModelsScanRequest.md)|  | 

### Return type

[**ScanResponse**](ScanResponse.md)

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

