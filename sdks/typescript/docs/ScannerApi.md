# ScannerApi

All URIs are relative to *http://localhost*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**scanApiV1ScannerScanPost**](ScannerApi.md#scanapiv1scannerscanpost) | **POST** /api/v1/scanner/scan | Scan |



## scanApiV1ScannerScanPost

> ScanResponse scanApiV1ScannerScanPost(appDomainScannerModelsScanRequest)

Scan

Full token scan. Multi-module. Returns Pydantic response.  The legacy /api/v1/scanner/scan (and /api/v1/token/scan) are still served — they take priority on the same path during strangelfig.

### Example

```ts
import {
  Configuration,
  ScannerApi,
} from '@rugmunch/sdk';
import type { ScanApiV1ScannerScanPostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new ScannerApi();

  const body = {
    // AppDomainScannerModelsScanRequest
    appDomainScannerModelsScanRequest: ...,
  } satisfies ScanApiV1ScannerScanPostRequest;

  try {
    const data = await api.scanApiV1ScannerScanPost(body);
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
| **appDomainScannerModelsScanRequest** | [AppDomainScannerModelsScanRequest](AppDomainScannerModelsScanRequest.md) |  | |

### Return type

[**ScanResponse**](ScanResponse.md)

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

