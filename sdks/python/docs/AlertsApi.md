# rugmunch.AlertsApi

All URIs are relative to *http://localhost*

Method | HTTP request | Description
------------- | ------------- | -------------
[**cancel_api_v1_alerts_alert_id_delete**](AlertsApi.md#cancel_api_v1_alerts_alert_id_delete) | **DELETE** /api/v1/alerts/{alert_id} | Cancel
[**list_alerts_api_v1_alerts_get**](AlertsApi.md#list_alerts_api_v1_alerts_get) | **GET** /api/v1/alerts | List Alerts
[**subscribe_api_v1_alerts_subscribe_post**](AlertsApi.md#subscribe_api_v1_alerts_subscribe_post) | **POST** /api/v1/alerts/subscribe | Subscribe


# **cancel_api_v1_alerts_alert_id_delete**
> cancel_api_v1_alerts_alert_id_delete(alert_id)

Cancel

Cancel (delete) a subscription. Owner only.

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
    api_instance = rugmunch.AlertsApi(api_client)
    alert_id = 'alert_id_example' # str | 

    try:
        # Cancel
        api_instance.cancel_api_v1_alerts_alert_id_delete(alert_id)
    except Exception as e:
        print("Exception when calling AlertsApi->cancel_api_v1_alerts_alert_id_delete: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **alert_id** | **str**|  | 

### Return type

void (empty response body)

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**204** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **list_alerts_api_v1_alerts_get**
> PaginatedResponse list_alerts_api_v1_alerts_get()

List Alerts

List alert subscriptions. Authenticated users see their own; anonymous sees all (legacy behavior).

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
    api_instance = rugmunch.AlertsApi(api_client)

    try:
        # List Alerts
        api_response = api_instance.list_alerts_api_v1_alerts_get()
        print("The response of AlertsApi->list_alerts_api_v1_alerts_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling AlertsApi->list_alerts_api_v1_alerts_get: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

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

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **subscribe_api_v1_alerts_subscribe_post**
> AlertSubscription subscribe_api_v1_alerts_subscribe_post(create_alert_request)

Subscribe

Create a new alert subscription for a token.

### Example


```python
import rugmunch
from rugmunch.models.alert_subscription import AlertSubscription
from rugmunch.models.create_alert_request import CreateAlertRequest
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
    api_instance = rugmunch.AlertsApi(api_client)
    create_alert_request = rugmunch.CreateAlertRequest() # CreateAlertRequest | 

    try:
        # Subscribe
        api_response = api_instance.subscribe_api_v1_alerts_subscribe_post(create_alert_request)
        print("The response of AlertsApi->subscribe_api_v1_alerts_subscribe_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling AlertsApi->subscribe_api_v1_alerts_subscribe_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **create_alert_request** | [**CreateAlertRequest**](CreateAlertRequest.md)|  | 

### Return type

[**AlertSubscription**](AlertSubscription.md)

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: application/json
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**201** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

