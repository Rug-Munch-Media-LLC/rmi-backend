# rugmunch.ReportsApi

All URIs are relative to *http://localhost*

Method | HTTP request | Description
------------- | ------------- | -------------
[**generate_report_api_v1_reports_generate_post**](ReportsApi.md#generate_report_api_v1_reports_generate_post) | **POST** /api/v1/reports/generate | Generate Report
[**get_report_api_v1_reports_report_id_get**](ReportsApi.md#get_report_api_v1_reports_report_id_get) | **GET** /api/v1/reports/{report_id} | Get Report


# **generate_report_api_v1_reports_generate_post**
> GenerateResponse generate_report_api_v1_reports_generate_post(generate_request)

Generate Report

Generate a research report for a token or wallet.

Composes 7 sections in parallel via LiteLLM. Falls back to templated
content if LLM is unreachable. Saves to Postgres on success.

### Example


```python
import rugmunch
from rugmunch.models.generate_request import GenerateRequest
from rugmunch.models.generate_response import GenerateResponse
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
    api_instance = rugmunch.ReportsApi(api_client)
    generate_request = rugmunch.GenerateRequest() # GenerateRequest | 

    try:
        # Generate Report
        api_response = api_instance.generate_report_api_v1_reports_generate_post(generate_request)
        print("The response of ReportsApi->generate_report_api_v1_reports_generate_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling ReportsApi->generate_report_api_v1_reports_generate_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **generate_request** | [**GenerateRequest**](GenerateRequest.md)|  | 

### Return type

[**GenerateResponse**](GenerateResponse.md)

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

# **get_report_api_v1_reports_report_id_get**
> Dict[str, object] get_report_api_v1_reports_report_id_get(report_id)

Get Report

Retrieve a previously generated report from Postgres.

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
    api_instance = rugmunch.ReportsApi(api_client)
    report_id = 'report_id_example' # str | 

    try:
        # Get Report
        api_response = api_instance.get_report_api_v1_reports_report_id_get(report_id)
        print("The response of ReportsApi->get_report_api_v1_reports_report_id_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling ReportsApi->get_report_api_v1_reports_report_id_get: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **report_id** | **str**|  | 

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

