
# NewsListResponse


## Properties

Name | Type
------------ | -------------
`items` | [Array&lt;NewsItemOut&gt;](NewsItemOut.md)
`total` | number
`offset` | number

## Example

```typescript
import type { NewsListResponse } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "items": null,
  "total": null,
  "offset": null,
} satisfies NewsListResponse

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as NewsListResponse
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


