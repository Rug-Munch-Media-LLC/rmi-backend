
# AppDomainScannerModelsScanRequest

Request body for the token scan endpoint.

## Properties

Name | Type
------------ | -------------
`address` | string
`chain` | string
`tier` | [ScanTier](ScanTier.md)
`userId` | string

## Example

```typescript
import type { AppDomainScannerModelsScanRequest } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "address": null,
  "chain": null,
  "tier": null,
  "userId": null,
} satisfies AppDomainScannerModelsScanRequest

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as AppDomainScannerModelsScanRequest
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


