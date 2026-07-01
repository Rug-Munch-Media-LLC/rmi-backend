
# IngestResult

RAG document ingestion result.

## Properties

Name | Type
------------ | -------------
`docId` | string
`collection` | string
`status` | string
`chunks` | number
`error` | string
`ingestedAt` | Date

## Example

```typescript
import type { IngestResult } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "docId": null,
  "collection": null,
  "status": null,
  "chunks": null,
  "error": null,
  "ingestedAt": null,
} satisfies IngestResult

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as IngestResult
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


