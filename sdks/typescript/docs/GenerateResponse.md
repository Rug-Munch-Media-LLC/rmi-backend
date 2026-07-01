
# GenerateResponse


## Properties

Name | Type
------------ | -------------
`reportId` | string
`subjectType` | string
`subjectId` | string
`riskScore` | number
`riskTier` | string
`riskFactors` | Array&lt;string&gt;
`generatedByModel` | string
`generatedAt` | string
`sections` | { [key: string]: string; }
`markdown` | string
`paidViaX402` | string
`error` | string

## Example

```typescript
import type { GenerateResponse } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "reportId": null,
  "subjectType": null,
  "subjectId": null,
  "riskScore": null,
  "riskTier": null,
  "riskFactors": null,
  "generatedByModel": null,
  "generatedAt": null,
  "sections": null,
  "markdown": null,
  "paidViaX402": null,
  "error": null,
} satisfies GenerateResponse

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as GenerateResponse
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


