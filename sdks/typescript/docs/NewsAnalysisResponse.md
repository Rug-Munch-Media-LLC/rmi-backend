
# NewsAnalysisResponse


## Properties

Name | Type
------------ | -------------
`newsId` | string
`analysis` | string
`model` | string
`error` | string

## Example

```typescript
import type { NewsAnalysisResponse } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "newsId": null,
  "analysis": null,
  "model": null,
  "error": null,
} satisfies NewsAnalysisResponse

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as NewsAnalysisResponse
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


