
# ScanResponse

Pydantic wrapper around the legacy ScanResult.  The legacy result is rich and complex (free/pro/elite per-tier data blocks, modules_run, confidence, etc.). We expose the most useful fields at the top level for API consumers.

## Properties

Name | Type
------------ | -------------
`address` | string
`chain` | string
`symbol` | string
`name` | string
`safetyScore` | number
`riskFlags` | Array&lt;string&gt;
`tierRequired` | string
`confidence` | number
`modulesRun` | [Array&lt;ScanModuleResult&gt;](ScanModuleResult.md)
`free` | { [key: string]: any; }
`pro` | { [key: string]: any; }
`elite` | { [key: string]: any; }
`scannedAt` | string

## Example

```typescript
import type { ScanResponse } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "address": null,
  "chain": null,
  "symbol": null,
  "name": null,
  "safetyScore": null,
  "riskFlags": null,
  "tierRequired": null,
  "confidence": null,
  "modulesRun": null,
  "free": null,
  "pro": null,
  "elite": null,
  "scannedAt": null,
} satisfies ScanResponse

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as ScanResponse
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


