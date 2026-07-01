# rugmunch.AdminAlertsApi

All URIs are relative to *http://localhost*

Method | HTTP request | Description
------------- | ------------- | -------------
[**alerts_critical_webhook_api_v1_admin_alerts_critical_post**](AdminAlertsApi.md#alerts_critical_webhook_api_v1_admin_alerts_critical_post) | **POST** /api/v1/admin/alerts/critical | Alerts Critical Webhook
[**alerts_recent_api_v1_admin_alerts_recent_get**](AdminAlertsApi.md#alerts_recent_api_v1_admin_alerts_recent_get) | **GET** /api/v1/admin/alerts/recent | Alerts Recent
[**alerts_webhook_api_v1_admin_alerts_webhook_post**](AdminAlertsApi.md#alerts_webhook_api_v1_admin_alerts_webhook_post) | **POST** /api/v1/admin/alerts/webhook | Alerts Webhook


# **alerts_critical_webhook_api_v1_admin_alerts_critical_post**
> Dict[str, object] alerts_critical_webhook_api_v1_admin_alerts_critical_post(alertmanager_payload)

Alerts Critical Webhook

Critical-only webhook — escalations only (severity=critical).

### Example


```python
import rugmunch
from rugmunch.models.alertmanager_payload import AlertmanagerPayload
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
    api_instance = rugmunch.AdminAlertsApi(api_client)
    alertmanager_payload = rugmunch.AlertmanagerPayload() # AlertmanagerPayload | 

    try:
        # Alerts Critical Webhook
        api_response = api_instance.alerts_critical_webhook_api_v1_admin_alerts_critical_post(alertmanager_payload)
        print("The response of AdminAlertsApi->alerts_critical_webhook_api_v1_admin_alerts_critical_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling AdminAlertsApi->alerts_critical_webhook_api_v1_admin_alerts_critical_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **alertmanager_payload** | [**AlertmanagerPayload**](AlertmanagerPayload.md)|  | 

### Return type

**Dict[str, object]**

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

# **alerts_recent_api_v1_admin_alerts_recent_get**
> Dict[str, object] alerts_recent_api_v1_admin_alerts_recent_get(limit=limit)

Alerts Recent

Read recent alerts (debug endpoint — admin only in production).

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
    api_instance = rugmunch.AdminAlertsApi(api_client)
    limit = 50 # int |  (optional) (default to 50)

    try:
        # Alerts Recent
        api_response = api_instance.alerts_recent_api_v1_admin_alerts_recent_get(limit=limit)
        print("The response of AdminAlertsApi->alerts_recent_api_v1_admin_alerts_recent_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling AdminAlertsApi->alerts_recent_api_v1_admin_alerts_recent_get: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **limit** | **int**|  | [optional] [default to 50]

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

# **alerts_webhook_api_v1_admin_alerts_webhook_post**
> Dict[str, object] alerts_webhook_api_v1_admin_alerts_webhook_post(alertmanager_payload)

Alerts Webhook

Default receiver webhook — all severities.

### Example


```python
import rugmunch
from rugmunch.models.alertmanager_payload import AlertmanagerPayload
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
    api_instance = rugmunch.AdminAlertsApi(api_client)
    alertmanager_payload = rugmunch.AlertmanagerPayload() # AlertmanagerPayload | 

    try:
        # Alerts Webhook
        api_response = api_instance.alerts_webhook_api_v1_admin_alerts_webhook_post(alertmanager_payload)
        print("The response of AdminAlertsApi->alerts_webhook_api_v1_admin_alerts_webhook_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling AdminAlertsApi->alerts_webhook_api_v1_admin_alerts_webhook_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **alertmanager_payload** | [**AlertmanagerPayload**](AlertmanagerPayload.md)|  | 

### Return type

**Dict[str, object]**

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

