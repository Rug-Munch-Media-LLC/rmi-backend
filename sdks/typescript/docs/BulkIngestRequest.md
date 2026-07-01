
# BulkIngestRequest


## Properties

Name | Type
------------ | -------------
`collection` | string
`items` | Array&lt;{ [key: string]: any; }&gt;

## Example

```typescript
import type { BulkIngestRequest } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "collection": null,
  "items": null,
} satisfies BulkIngestRequest

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as BulkIngestRequest
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


