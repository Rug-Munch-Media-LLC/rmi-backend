
# SearchHit

A single search result.

## Properties

Name | Type
------------ | -------------
`content` | string
`score` | number
`metadata` | { [key: string]: any; }
`collection` | string
`docId` | string

## Example

```typescript
import type { SearchHit } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "content": null,
  "score": null,
  "metadata": null,
  "collection": null,
  "docId": null,
} satisfies SearchHit

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as SearchHit
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


