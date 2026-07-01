# NewsAdminApi

All URIs are relative to *http://localhost*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**triggerIngestApiV1NewsAdminIngestPost**](NewsAdminApi.md#triggeringestapiv1newsadminingestpost) | **POST** /api/v1/news/_admin/ingest | Trigger Ingest |



## triggerIngestApiV1NewsAdminIngestPost

> { [key: string]: any; } triggerIngestApiV1NewsAdminIngestPost()

Trigger Ingest

Trigger RSS ingest now (synchronous). Returns counts.

### Example

```ts
import {
  Configuration,
  NewsAdminApi,
} from '@rugmunch/sdk';
import type { TriggerIngestApiV1NewsAdminIngestPostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new NewsAdminApi();

  try {
    const data = await api.triggerIngestApiV1NewsAdminIngestPost();
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

