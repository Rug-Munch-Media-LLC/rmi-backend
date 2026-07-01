# rugmunch.NewsApi

All URIs are relative to *http://localhost*

Method | HTTP request | Description
------------- | ------------- | -------------
[**analyze_news_api_v1_news_news_id_analyze_post**](NewsApi.md#analyze_news_api_v1_news_news_id_analyze_post) | **POST** /api/v1/news/{news_id}/analyze | Analyze News
[**get_news_api_v1_news_news_id_get**](NewsApi.md#get_news_api_v1_news_news_id_get) | **GET** /api/v1/news/{news_id} | Get News
[**list_news_api_v1_news_post**](NewsApi.md#list_news_api_v1_news_post) | **POST** /api/v1/news | List News
[**trending_news_api_v1_news_trending_get**](NewsApi.md#trending_news_api_v1_news_trending_get) | **GET** /api/v1/news/trending | Trending News


# **analyze_news_api_v1_news_news_id_analyze_post**
> NewsAnalysisResponse analyze_news_api_v1_news_news_id_analyze_post(news_id)

Analyze News

Generate LLM analysis via LiteLLM. Falls back to None if LLM unreachable.

### Example


```python
import rugmunch
from rugmunch.models.news_analysis_response import NewsAnalysisResponse
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
    api_instance = rugmunch.NewsApi(api_client)
    news_id = 'news_id_example' # str | 

    try:
        # Analyze News
        api_response = api_instance.analyze_news_api_v1_news_news_id_analyze_post(news_id)
        print("The response of NewsApi->analyze_news_api_v1_news_news_id_analyze_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling NewsApi->analyze_news_api_v1_news_news_id_analyze_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **news_id** | **str**|  | 

### Return type

[**NewsAnalysisResponse**](NewsAnalysisResponse.md)

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

# **get_news_api_v1_news_news_id_get**
> NewsItemOut get_news_api_v1_news_news_id_get(news_id)

Get News

Single news item. Searches both news_items and crypto_news.

### Example


```python
import rugmunch
from rugmunch.models.news_item_out import NewsItemOut
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
    api_instance = rugmunch.NewsApi(api_client)
    news_id = 'news_id_example' # str | 

    try:
        # Get News
        api_response = api_instance.get_news_api_v1_news_news_id_get(news_id)
        print("The response of NewsApi->get_news_api_v1_news_news_id_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling NewsApi->get_news_api_v1_news_news_id_get: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **news_id** | **str**|  | 

### Return type

[**NewsItemOut**](NewsItemOut.md)

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

# **list_news_api_v1_news_post**
> NewsListResponse list_news_api_v1_news_post(chain=chain, token=token, category=category, since_hours=since_hours, limit=limit, offset=offset, sort=sort)

List News

List news items with filters. Reads from both news_items (new) and crypto_news (legacy).

### Example


```python
import rugmunch
from rugmunch.models.news_list_response import NewsListResponse
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
    api_instance = rugmunch.NewsApi(api_client)
    chain = 'chain_example' # str |  (optional)
    token = 'token_example' # str |  (optional)
    category = 'category_example' # str |  (optional)
    since_hours = 24 # int |  (optional) (default to 24)
    limit = 20 # int |  (optional) (default to 20)
    offset = 0 # int |  (optional) (default to 0)
    sort = 'recency' # str |  (optional) (default to 'recency')

    try:
        # List News
        api_response = api_instance.list_news_api_v1_news_post(chain=chain, token=token, category=category, since_hours=since_hours, limit=limit, offset=offset, sort=sort)
        print("The response of NewsApi->list_news_api_v1_news_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling NewsApi->list_news_api_v1_news_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **chain** | **str**|  | [optional] 
 **token** | **str**|  | [optional] 
 **category** | **str**|  | [optional] 
 **since_hours** | **int**|  | [optional] [default to 24]
 **limit** | **int**|  | [optional] [default to 20]
 **offset** | **int**|  | [optional] [default to 0]
 **sort** | **str**|  | [optional] [default to &#39;recency&#39;]

### Return type

[**NewsListResponse**](NewsListResponse.md)

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

# **trending_news_api_v1_news_trending_get**
> NewsListResponse trending_news_api_v1_news_trending_get(window_hours=window_hours, limit=limit)

Trending News

Time-decay trending. Reads from news_items (new) primary, falls back to crypto_news (legacy).

### Example


```python
import rugmunch
from rugmunch.models.news_list_response import NewsListResponse
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
    api_instance = rugmunch.NewsApi(api_client)
    window_hours = 168 # int |  (optional) (default to 168)
    limit = 20 # int |  (optional) (default to 20)

    try:
        # Trending News
        api_response = api_instance.trending_news_api_v1_news_trending_get(window_hours=window_hours, limit=limit)
        print("The response of NewsApi->trending_news_api_v1_news_trending_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling NewsApi->trending_news_api_v1_news_trending_get: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **window_hours** | **int**|  | [optional] [default to 168]
 **limit** | **int**|  | [optional] [default to 20]

### Return type

[**NewsListResponse**](NewsListResponse.md)

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

