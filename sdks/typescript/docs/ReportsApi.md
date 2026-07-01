# ReportsApi

All URIs are relative to *http://localhost*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**generateReportApiV1ReportsGeneratePost**](ReportsApi.md#generatereportapiv1reportsgeneratepost) | **POST** /api/v1/reports/generate | Generate Report |
| [**getReportApiV1ReportsReportIdGet**](ReportsApi.md#getreportapiv1reportsreportidget) | **GET** /api/v1/reports/{report_id} | Get Report |



## generateReportApiV1ReportsGeneratePost

> GenerateResponse generateReportApiV1ReportsGeneratePost(generateRequest)

Generate Report

Generate a research report for a token or wallet.  Composes 7 sections in parallel via LiteLLM. Falls back to templated content if LLM is unreachable. Saves to Postgres on success.

### Example

```ts
import {
  Configuration,
  ReportsApi,
} from '@rugmunch/sdk';
import type { GenerateReportApiV1ReportsGeneratePostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new ReportsApi();

  const body = {
    // GenerateRequest
    generateRequest: ...,
  } satisfies GenerateReportApiV1ReportsGeneratePostRequest;

  try {
    const data = await api.generateReportApiV1ReportsGeneratePost(body);
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
| **generateRequest** | [GenerateRequest](GenerateRequest.md) |  | |

### Return type

[**GenerateResponse**](GenerateResponse.md)

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


## getReportApiV1ReportsReportIdGet

> { [key: string]: any; } getReportApiV1ReportsReportIdGet(reportId)

Get Report

Retrieve a previously generated report from Postgres.

### Example

```ts
import {
  Configuration,
  ReportsApi,
} from '@rugmunch/sdk';
import type { GetReportApiV1ReportsReportIdGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new ReportsApi();

  const body = {
    // string
    reportId: reportId_example,
  } satisfies GetReportApiV1ReportsReportIdGetRequest;

  try {
    const data = await api.getReportApiV1ReportsReportIdGet(body);
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
| **reportId** | `string` |  | [Defaults to `undefined`] |

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
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)

