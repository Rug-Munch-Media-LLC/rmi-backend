# MetaApi

All URIs are relative to *http://localhost*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**versionVersionGet**](MetaApi.md#versionversionget) | **GET** /version | Version |



## versionVersionGet

> { [key: string]: any; } versionVersionGet()

Version

Backend version + git info.

### Example

```ts
import {
  Configuration,
  MetaApi,
} from '@rugmunch/sdk';
import type { VersionVersionGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new MetaApi();

  try {
    const data = await api.versionVersionGet();
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

