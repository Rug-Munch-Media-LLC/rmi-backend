
# SearchRequest

RAG search request.

## Properties

Name | Type
------------ | -------------
`query` | string
`collection` | string
`topK` | number
`minSimilarity` | number
`filters` | { [key: string]: any; }

## Example

```typescript
import type { SearchRequest } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "query": null,
  "collection": null,
  "topK": null,
  "minSimilarity": null,
  "filters": null,
} satisfies SearchRequest

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as SearchRequest
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


