# rugmunch.RagApi

All URIs are relative to *http://localhost*

Method | HTTP request | Description
------------- | ------------- | -------------
[**bulk_api_v1_rag_v2_bulk_ingest_post**](RagApi.md#bulk_api_v1_rag_v2_bulk_ingest_post) | **POST** /api/v1/rag/v2/bulk-ingest | Bulk
[**feedback_api_v1_rag_v2_feedback_post**](RagApi.md#feedback_api_v1_rag_v2_feedback_post) | **POST** /api/v1/rag/v2/feedback | Feedback
[**ingest_api_v1_rag_v2_ingest_post**](RagApi.md#ingest_api_v1_rag_v2_ingest_post) | **POST** /api/v1/rag/v2/ingest | Ingest
[**search_api_v1_rag_v2_search_post**](RagApi.md#search_api_v1_rag_v2_search_post) | **POST** /api/v1/rag/v2/search | Search
[**stats_api_v1_rag_v2_stats_get**](RagApi.md#stats_api_v1_rag_v2_stats_get) | **GET** /api/v1/rag/v2/stats | Stats


# **bulk_api_v1_rag_v2_bulk_ingest_post**
> Dict[str, object] bulk_api_v1_rag_v2_bulk_ingest_post(bulk_ingest_request)

Bulk

Ingest many items into a collection sequentially (max 500 per call).

### Example


```python
import rugmunch
from rugmunch.models.bulk_ingest_request import BulkIngestRequest
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
    api_instance = rugmunch.RagApi(api_client)
    bulk_ingest_request = rugmunch.BulkIngestRequest() # BulkIngestRequest | 

    try:
        # Bulk
        api_response = api_instance.bulk_api_v1_rag_v2_bulk_ingest_post(bulk_ingest_request)
        print("The response of RagApi->bulk_api_v1_rag_v2_bulk_ingest_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling RagApi->bulk_api_v1_rag_v2_bulk_ingest_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **bulk_ingest_request** | [**BulkIngestRequest**](BulkIngestRequest.md)|  | 

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

# **feedback_api_v1_rag_v2_feedback_post**
> IngestResult feedback_api_v1_rag_v2_feedback_post(feedback_record)

Feedback

Record scanner → RAG feedback. Ingests known scam into known_scams collection.

### Example


```python
import rugmunch
from rugmunch.models.feedback_record import FeedbackRecord
from rugmunch.models.ingest_result import IngestResult
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
    api_instance = rugmunch.RagApi(api_client)
    feedback_record = rugmunch.FeedbackRecord() # FeedbackRecord | 

    try:
        # Feedback
        api_response = api_instance.feedback_api_v1_rag_v2_feedback_post(feedback_record)
        print("The response of RagApi->feedback_api_v1_rag_v2_feedback_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling RagApi->feedback_api_v1_rag_v2_feedback_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **feedback_record** | [**FeedbackRecord**](FeedbackRecord.md)|  | 

### Return type

[**IngestResult**](IngestResult.md)

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

# **ingest_api_v1_rag_v2_ingest_post**
> IngestResult ingest_api_v1_rag_v2_ingest_post(ingest_request)

Ingest

Ingest a document into the RAG system.

### Example


```python
import rugmunch
from rugmunch.models.ingest_request import IngestRequest
from rugmunch.models.ingest_result import IngestResult
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
    api_instance = rugmunch.RagApi(api_client)
    ingest_request = rugmunch.IngestRequest() # IngestRequest | 

    try:
        # Ingest
        api_response = api_instance.ingest_api_v1_rag_v2_ingest_post(ingest_request)
        print("The response of RagApi->ingest_api_v1_rag_v2_ingest_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling RagApi->ingest_api_v1_rag_v2_ingest_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **ingest_request** | [**IngestRequest**](IngestRequest.md)|  | 

### Return type

[**IngestResult**](IngestResult.md)

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

# **search_api_v1_rag_v2_search_post**
> SearchResponse search_api_v1_rag_v2_search_post(search_request)

Search

RAG search. Returns Pydantic response with hits + scores.

### Example


```python
import rugmunch
from rugmunch.models.search_request import SearchRequest
from rugmunch.models.search_response import SearchResponse
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
    api_instance = rugmunch.RagApi(api_client)
    search_request = rugmunch.SearchRequest() # SearchRequest | 

    try:
        # Search
        api_response = api_instance.search_api_v1_rag_v2_search_post(search_request)
        print("The response of RagApi->search_api_v1_rag_v2_search_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling RagApi->search_api_v1_rag_v2_search_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **search_request** | [**SearchRequest**](SearchRequest.md)|  | 

### Return type

[**SearchResponse**](SearchResponse.md)

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

# **stats_api_v1_rag_v2_stats_get**
> Dict[str, object] stats_api_v1_rag_v2_stats_get()

Stats

Per-collection vector counts + active embedder backend.

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
    api_instance = rugmunch.RagApi(api_client)

    try:
        # Stats
        api_response = api_instance.stats_api_v1_rag_v2_stats_get()
        print("The response of RagApi->stats_api_v1_rag_v2_stats_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling RagApi->stats_api_v1_rag_v2_stats_get: %s\n" % e)
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

