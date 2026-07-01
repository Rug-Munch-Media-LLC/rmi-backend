# rugmunch.NewsAdminApi

All URIs are relative to *http://localhost*

Method | HTTP request | Description
------------- | ------------- | -------------
[**trigger_ingest_api_v1_news_admin_ingest_post**](NewsAdminApi.md#trigger_ingest_api_v1_news_admin_ingest_post) | **POST** /api/v1/news/_admin/ingest | Trigger Ingest


# **trigger_ingest_api_v1_news_admin_ingest_post**
> Dict[str, object] trigger_ingest_api_v1_news_admin_ingest_post()

Trigger Ingest

Trigger RSS ingest now (synchronous). Returns counts.

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
    api_instance = rugmunch.NewsAdminApi(api_client)

    try:
        # Trigger Ingest
        api_response = api_instance.trigger_ingest_api_v1_news_admin_ingest_post()
        print("The response of NewsAdminApi->trigger_ingest_api_v1_news_admin_ingest_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling NewsAdminApi->trigger_ingest_api_v1_news_admin_ingest_post: %s\n" % e)
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

