# RagApi

All URIs are relative to *http://localhost*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**bulkApiV1RagV2BulkIngestPost**](RagApi.md#bulkapiv1ragv2bulkingestpost) | **POST** /api/v1/rag/v2/bulk-ingest | Bulk |
| [**feedbackApiV1RagV2FeedbackPost**](RagApi.md#feedbackapiv1ragv2feedbackpost) | **POST** /api/v1/rag/v2/feedback | Feedback |
| [**ingestApiV1RagV2IngestPost**](RagApi.md#ingestapiv1ragv2ingestpost) | **POST** /api/v1/rag/v2/ingest | Ingest |
| [**searchApiV1RagV2SearchPost**](RagApi.md#searchapiv1ragv2searchpost) | **POST** /api/v1/rag/v2/search | Search |
| [**statsApiV1RagV2StatsGet**](RagApi.md#statsapiv1ragv2statsget) | **GET** /api/v1/rag/v2/stats | Stats |



## bulkApiV1RagV2BulkIngestPost

> { [key: string]: any; } bulkApiV1RagV2BulkIngestPost(bulkIngestRequest)

Bulk

Ingest many items into a collection sequentially (max 500 per call).

### Example

```ts
import {
  Configuration,
  RagApi,
} from '@rugmunch/sdk';
import type { BulkApiV1RagV2BulkIngestPostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new RagApi();

  const body = {
    // BulkIngestRequest
    bulkIngestRequest: ...,
  } satisfies BulkApiV1RagV2BulkIngestPostRequest;

  try {
    const data = await api.bulkApiV1RagV2BulkIngestPost(body);
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
| **bulkIngestRequest** | [BulkIngestRequest](BulkIngestRequest.md) |  | |

### Return type

**{ [key: string]: any; }**

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: `application/json`
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## feedbackApiV1RagV2FeedbackPost

> IngestResult feedbackApiV1RagV2FeedbackPost(feedbackRecord)

Feedback

Record scanner → RAG feedback. Ingests known scam into known_scams collection.

### Example

```ts
import {
  Configuration,
  RagApi,
} from '@rugmunch/sdk';
import type { FeedbackApiV1RagV2FeedbackPostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new RagApi();

  const body = {
    // FeedbackRecord
    feedbackRecord: ...,
  } satisfies FeedbackApiV1RagV2FeedbackPostRequest;

  try {
    const data = await api.feedbackApiV1RagV2FeedbackPost(body);
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
| **feedbackRecord** | [FeedbackRecord](FeedbackRecord.md) |  | |

### Return type

[**IngestResult**](IngestResult.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: `application/json`
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## ingestApiV1RagV2IngestPost

> IngestResult ingestApiV1RagV2IngestPost(ingestRequest)

Ingest

Ingest a document into the RAG system.

### Example

```ts
import {
  Configuration,
  RagApi,
} from '@rugmunch/sdk';
import type { IngestApiV1RagV2IngestPostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new RagApi();

  const body = {
    // IngestRequest
    ingestRequest: ...,
  } satisfies IngestApiV1RagV2IngestPostRequest;

  try {
    const data = await api.ingestApiV1RagV2IngestPost(body);
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
| **ingestRequest** | [IngestRequest](IngestRequest.md) |  | |

### Return type

[**IngestResult**](IngestResult.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: `application/json`
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## searchApiV1RagV2SearchPost

> SearchResponse searchApiV1RagV2SearchPost(searchRequest)

Search

RAG search. Returns Pydantic response with hits + scores.

### Example

```ts
import {
  Configuration,
  RagApi,
} from '@rugmunch/sdk';
import type { SearchApiV1RagV2SearchPostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new RagApi();

  const body = {
    // SearchRequest
    searchRequest: ...,
  } satisfies SearchApiV1RagV2SearchPostRequest;

  try {
    const data = await api.searchApiV1RagV2SearchPost(body);
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
| **searchRequest** | [SearchRequest](SearchRequest.md) |  | |

### Return type

[**SearchResponse**](SearchResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: `application/json`
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## statsApiV1RagV2StatsGet

> { [key: string]: any; } statsApiV1RagV2StatsGet()

Stats

Per-collection vector counts + active embedder backend.

### Example

```ts
import {
  Configuration,
  RagApi,
} from '@rugmunch/sdk';
import type { StatsApiV1RagV2StatsGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new RagApi();

  try {
    const data = await api.statsApiV1RagV2StatsGet();
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters

This endpoint does not need any parameter.

### Return type

**{ [key: string]: any; }**

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)

