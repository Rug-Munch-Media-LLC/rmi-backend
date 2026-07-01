# NewsApi

All URIs are relative to *http://localhost*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**analyzeNewsApiV1NewsNewsIdAnalyzePost**](NewsApi.md#analyzenewsapiv1newsnewsidanalyzepost) | **POST** /api/v1/news/{news_id}/analyze | Analyze News |
| [**getNewsApiV1NewsNewsIdGet**](NewsApi.md#getnewsapiv1newsnewsidget) | **GET** /api/v1/news/{news_id} | Get News |
| [**listNewsApiV1NewsPost**](NewsApi.md#listnewsapiv1newspost) | **POST** /api/v1/news | List News |
| [**trendingNewsApiV1NewsTrendingGet**](NewsApi.md#trendingnewsapiv1newstrendingget) | **GET** /api/v1/news/trending | Trending News |



## analyzeNewsApiV1NewsNewsIdAnalyzePost

> NewsAnalysisResponse analyzeNewsApiV1NewsNewsIdAnalyzePost(newsId)

Analyze News

Generate LLM analysis via LiteLLM. Falls back to None if LLM unreachable.

### Example

```ts
import {
  Configuration,
  NewsApi,
} from '@rugmunch/sdk';
import type { AnalyzeNewsApiV1NewsNewsIdAnalyzePostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new NewsApi();

  const body = {
    // string
    newsId: newsId_example,
  } satisfies AnalyzeNewsApiV1NewsNewsIdAnalyzePostRequest;

  try {
    const data = await api.analyzeNewsApiV1NewsNewsIdAnalyzePost(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **newsId** | `string` |  | [Defaults to `undefined`] |

### Return type

[**NewsAnalysisResponse**](NewsAnalysisResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## getNewsApiV1NewsNewsIdGet

> NewsItemOut getNewsApiV1NewsNewsIdGet(newsId)

Get News

Single news item. Searches both news_items and crypto_news.

### Example

```ts
import {
  Configuration,
  NewsApi,
} from '@rugmunch/sdk';
import type { GetNewsApiV1NewsNewsIdGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new NewsApi();

  const body = {
    // string
    newsId: newsId_example,
  } satisfies GetNewsApiV1NewsNewsIdGetRequest;

  try {
    const data = await api.getNewsApiV1NewsNewsIdGet(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **newsId** | `string` |  | [Defaults to `undefined`] |

### Return type

[**NewsItemOut**](NewsItemOut.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## listNewsApiV1NewsPost

> NewsListResponse listNewsApiV1NewsPost(chain, token, category, sinceHours, limit, offset, sort)

List News

List news items with filters. Reads from both news_items (new) and crypto_news (legacy).

### Example

```ts
import {
  Configuration,
  NewsApi,
} from '@rugmunch/sdk';
import type { ListNewsApiV1NewsPostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new NewsApi();

  const body = {
    // string (optional)
    chain: chain_example,
    // string (optional)
    token: token_example,
    // string (optional)
    category: category_example,
    // number (optional)
    sinceHours: 56,
    // number (optional)
    limit: 56,
    // number (optional)
    offset: 56,
    // string (optional)
    sort: sort_example,
  } satisfies ListNewsApiV1NewsPostRequest;

  try {
    const data = await api.listNewsApiV1NewsPost(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **chain** | `string` |  | [Optional] [Defaults to `undefined`] |
| **token** | `string` |  | [Optional] [Defaults to `undefined`] |
| **category** | `string` |  | [Optional] [Defaults to `undefined`] |
| **sinceHours** | `number` |  | [Optional] [Defaults to `24`] |
| **limit** | `number` |  | [Optional] [Defaults to `20`] |
| **offset** | `number` |  | [Optional] [Defaults to `0`] |
| **sort** | `string` |  | [Optional] [Defaults to `&#39;recency&#39;`] |

### Return type

[**NewsListResponse**](NewsListResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## trendingNewsApiV1NewsTrendingGet

> NewsListResponse trendingNewsApiV1NewsTrendingGet(windowHours, limit)

Trending News

Time-decay trending. Reads from news_items (new) primary, falls back to crypto_news (legacy).

### Example

```ts
import {
  Configuration,
  NewsApi,
} from '@rugmunch/sdk';
import type { TrendingNewsApiV1NewsTrendingGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new NewsApi();

  const body = {
    // number (optional)
    windowHours: 56,
    // number (optional)
    limit: 56,
  } satisfies TrendingNewsApiV1NewsTrendingGetRequest;

  try {
    const data = await api.trendingNewsApiV1NewsTrendingGet(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **windowHours** | `number` |  | [Optional] [Defaults to `168`] |
| **limit** | `number` |  | [Optional] [Defaults to `20`] |

### Return type

[**NewsListResponse**](NewsListResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)

