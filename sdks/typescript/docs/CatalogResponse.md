
# CatalogResponse


## Properties

Name | Type
------------ | -------------
`server` | string
`version` | string
`tools` | Array&lt;{ [key: string]: any; }&gt;
`paymentProtocol` | string

## Example

```typescript
import type { CatalogResponse } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "server": null,
  "version": null,
  "tools": null,
  "paymentProtocol": null,
} satisfies CatalogResponse

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as CatalogResponse
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


