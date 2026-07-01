# Transaction

A wallet transaction summary.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**hash** | **str** |  | 
**type** | **str** |  | [optional] [default to 'transfer']
**amount_usd** | **float** |  | [optional] [default to 0.0]
**timestamp** | **int** |  | [optional] [default to 0]
**block_time** | **datetime** |  | [optional] 
**direction** | **str** | in | out | self | [optional] 

## Example

```python
from rugmunch.models.transaction import Transaction

# TODO update the JSON string below
json = "{}"
# create an instance of Transaction from a JSON string
transaction_instance = Transaction.from_json(json)
# print the JSON string representation of the object
print(Transaction.to_json())

# convert the object into a dict
transaction_dict = transaction_instance.to_dict()
# create an instance of Transaction from a dict
transaction_from_dict = Transaction.from_dict(transaction_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


